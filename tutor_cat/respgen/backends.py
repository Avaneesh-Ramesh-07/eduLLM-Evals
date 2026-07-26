"""Generation backends. Heavy deps (vllm, torch, transformers) import LAZILY
inside methods, so the rest of respgen loads on the Windows dev box (which has
none of them).

One model per process, tensor_parallel_size=1: each <=3B model fits a single
B200, so the orchestrator runs 8 of these processes (one per GPU, data
parallelism) rather than sharding one model across GPUs.

VLLMBackend serves the common case with true batched decode. HFBackend is the
fallback for architectures vLLM can't serve (mamba/OpenELM/hybrid SSM) and for
encoder-decoder (flan-t5) models via AutoModelForSeq2SeqLM.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GenParams:
    temperature: float = 0.0
    top_p: float = 1.0
    max_new_tokens: int = 4096
    repetition_penalty: float = 1.1
    seed: int = 0


@dataclass
class GenResult:
    text: str
    output_tokens: int
    finish_reason: str            # "stop" | "length" | "error"
    latency_s: float | None = None
    prompt_tokens: int | None = None


def _norm_finish(reason: str | None) -> str:
    """Map a backend's finish reason onto {stop, length}."""
    return "length" if reason == "length" else "stop"


class VLLMBackend:
    """vLLM offline batched inference. Prompts are pre-rendered strings; vLLM
    tokenizes and batches them internally (GEMV -> GEMM, HBM-bandwidth bound)."""

    def __init__(
        self,
        model_id: str,
        revision: str | None = None,
        max_model_len: int = 32768,
        dtype: str = "auto",
    ):
        from vllm import LLM  # lazy

        self.model_id = model_id
        self.llm = LLM(
            model=model_id,
            revision=revision,
            tensor_parallel_size=1,
            max_model_len=max_model_len,
            dtype=dtype,
            trust_remote_code=True,
        )
        self.tokenizer = self.llm.get_tokenizer()

    def generate(self, prompts: list[str], params: GenParams) -> list[GenResult]:
        from vllm import SamplingParams  # lazy

        sp = SamplingParams(
            temperature=params.temperature,
            top_p=params.top_p,
            max_tokens=params.max_new_tokens,
            repetition_penalty=params.repetition_penalty,
            seed=params.seed,
        )
        outputs = self.llm.generate(prompts, sp)
        results: list[GenResult] = []
        for out in outputs:
            comp = out.outputs[0]
            # Per-request latency from vLLM metrics when available; batching makes
            # per-scenario wall-clock otherwise ill-defined (see README note).
            latency = None
            metrics = getattr(out, "metrics", None)
            if metrics is not None and getattr(metrics, "finished_time", None) and getattr(metrics, "arrival_time", None):
                latency = float(metrics.finished_time - metrics.arrival_time)
            results.append(
                GenResult(
                    text=comp.text,
                    output_tokens=len(comp.token_ids),
                    finish_reason=_norm_finish(comp.finish_reason),
                    latency_s=latency,
                    prompt_tokens=len(out.prompt_token_ids),
                )
            )
        return results


class HFBackend:
    """transformers fallback. Simple per-prompt generate loop — used only for the
    handful of models vLLM can't serve, so throughput isn't the concern."""

    def __init__(
        self,
        model_id: str,
        revision: str | None = None,
        max_model_len: int = 32768,
        architecture: str = "causal",
        device: str = "cuda",
        tokenizer_id: str | None = None,
    ):
        import torch  # lazy
        from transformers import AutoTokenizer  # lazy

        self.model_id = model_id
        self.architecture = architecture
        self.max_model_len = max_model_len
        self.device = device
        self._torch = torch

        # Models that ship no tokenizer (OpenELM) point tokenizer_id at an
        # ungated mirror; the tokenizer revision must NOT be the model's SHA, so
        # only pin the revision when loading from the model repo itself.
        tok_src = tokenizer_id or model_id
        tok_revision = revision if tokenizer_id is None else None
        self.tokenizer = AutoTokenizer.from_pretrained(
            tok_src, revision=tok_revision, trust_remote_code=True
        )
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        if architecture == "seq2seq":
            from transformers import AutoModelForSeq2SeqLM

            loader = AutoModelForSeq2SeqLM
        else:
            from transformers import AutoModelForCausalLM

            loader = AutoModelForCausalLM
        self.model = loader.from_pretrained(
            model_id,
            revision=revision,
            torch_dtype="auto",
            trust_remote_code=True,
            device_map=device,
        )
        self.model.eval()

    def generate(self, prompts: list[str], params: GenParams) -> list[GenResult]:
        import time

        torch = self._torch
        greedy = params.temperature == 0.0
        gen_kwargs = dict(
            max_new_tokens=params.max_new_tokens,
            repetition_penalty=params.repetition_penalty,
            pad_token_id=self.tokenizer.pad_token_id,
            do_sample=not greedy,
        )
        if not greedy:
            gen_kwargs.update(temperature=params.temperature, top_p=params.top_p)

        # Reserve the generation budget so the input never crowds out the output.
        input_cap = max(1, self.max_model_len - params.max_new_tokens)
        results: list[GenResult] = []
        for prompt in prompts:
            t0 = time.time()
            enc = self.tokenizer(
                prompt, return_tensors="pt", truncation=True, max_length=input_cap
            ).to(self.model.device)
            n_in = int(enc["input_ids"].shape[1])
            with torch.no_grad():
                out = self.model.generate(**enc, **gen_kwargs)
            # seq2seq returns only new tokens; causal LM returns prompt + new.
            gen_ids = out[0] if self.architecture == "seq2seq" else out[0][n_in:]
            text = self.tokenizer.decode(gen_ids, skip_special_tokens=True)
            n_out = int(gen_ids.shape[0])
            results.append(
                GenResult(
                    text=text,
                    output_tokens=n_out,
                    finish_reason="length" if n_out >= params.max_new_tokens else "stop",
                    latency_s=time.time() - t0,
                    prompt_tokens=n_in,
                )
            )
        return results
