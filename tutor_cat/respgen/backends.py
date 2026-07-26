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
        gpu_memory_utilization: float | None = None,
        enforce_eager: bool = False,
    ):
        from vllm import LLM  # lazy

        self.model_id = model_id
        kwargs: dict = dict(
            model=model_id,
            revision=revision,
            tensor_parallel_size=1,
            max_model_len=max_model_len,
            dtype=dtype,
            trust_remote_code=True,
            enforce_eager=enforce_eager,
        )
        # Only pass gpu_memory_utilization when set, so the vLLM default (0.9) is
        # used unless a model explicitly needs a smaller reservation (shared GPU).
        if gpu_memory_utilization is not None:
            kwargs["gpu_memory_utilization"] = gpu_memory_utilization
        self.llm = LLM(**kwargs)
        self.tokenizer = self.llm.get_tokenizer()

    def generate(
        self,
        prompts: list[str],
        params: GenParams,
        max_tokens_per_prompt: list[int] | None = None,
    ) -> list[GenResult]:
        from vllm import SamplingParams  # lazy

        def _sp(max_tokens: int) -> "SamplingParams":
            return SamplingParams(
                temperature=params.temperature,
                top_p=params.top_p,
                max_tokens=max_tokens,
                repetition_penalty=params.repetition_penalty,
                seed=params.seed,
            )

        # vLLM accepts a list of SamplingParams aligned to prompts, so each
        # scenario decodes with its own budget (short prompt -> full budget; long
        # prompt on a small window -> whatever context is left, >= MIN_GEN).
        if max_tokens_per_prompt is None:
            sp = _sp(params.max_new_tokens)
        else:
            sp = [_sp(mt) for mt in max_tokens_per_prompt]
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

        # Some repos ship no tokenizer (e.g. apple/OpenELM expects Llama-2's).
        # When borrowing another repo's tokenizer, don't reuse this model's
        # revision SHA — it doesn't exist in the tokenizer repo.
        tok_src = tokenizer_id or model_id
        tok_rev = revision if tok_src == model_id else None
        self.tokenizer = AutoTokenizer.from_pretrained(
            tok_src, revision=tok_rev, trust_remote_code=True
        )
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        # Match the runner's tail-keeping truncation (ids[-cap:]): if a re-encode
        # of the already-fit prompt drifts a token or two over input_cap, drop from
        # the HEAD so the generation cue ("Tutor:"/chat gen token) at the tail — the
        # part that makes the model answer — always survives. HF defaults to "right"
        # (tail-dropping), the opposite; set it explicitly.
        self.tokenizer.truncation_side = "left"

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

    def generate(
        self,
        prompts: list[str],
        params: GenParams,
        max_tokens_per_prompt: list[int] | None = None,
    ) -> list[GenResult]:
        import time

        torch = self._torch
        greedy = params.temperature == 0.0
        base_kwargs = dict(
            repetition_penalty=params.repetition_penalty,
            pad_token_id=self.tokenizer.pad_token_id,
            do_sample=not greedy,
        )
        if not greedy:
            base_kwargs.update(temperature=params.temperature, top_p=params.top_p)

        results: list[GenResult] = []
        for i, prompt in enumerate(prompts):
            mt = params.max_new_tokens if max_tokens_per_prompt is None else max_tokens_per_prompt[i]
            # Reserve this scenario's generation budget so the input never crowds
            # out the output (the runner already fit the prompt; this is the hard
            # guarantee against decode->re-encode drift). max(1, ...) is safe now
            # because mt <= max_model_len - prompt_tokens, so input_cap >= prompt.
            input_cap = max(1, self.max_model_len - mt)
            t0 = time.time()
            enc = self.tokenizer(
                prompt, return_tensors="pt", truncation=True, max_length=input_cap
            ).to(self.model.device)
            n_in = int(enc["input_ids"].shape[1])
            with torch.no_grad():
                out = self.model.generate(**enc, max_new_tokens=mt, **base_kwargs)
            # seq2seq returns only new tokens; causal LM returns prompt + new.
            gen_ids = out[0] if self.architecture == "seq2seq" else out[0][n_in:]
            text = self.tokenizer.decode(gen_ids, skip_special_tokens=True)
            n_out = int(gen_ids.shape[0])
            results.append(
                GenResult(
                    text=text,
                    output_tokens=n_out,
                    finish_reason="length" if n_out >= mt else "stop",
                    latency_s=time.time() - t0,
                    prompt_tokens=n_in,
                )
            )
        return results
