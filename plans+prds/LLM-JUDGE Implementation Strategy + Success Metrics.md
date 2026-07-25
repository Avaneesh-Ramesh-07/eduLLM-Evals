## Objective

Our original proposal bypassed the LLM Judge step by only dealing with multiple-choice questions (MCQs). However, FRQs make up a substantial part of student practice questions across several disciplines, and using LLM Judge would enable us to grade MCQs and FRQs.  
The leading issue with FRQs is the fact that they aren’t deterministic, and therefore, **we need a reliable way for an LLM Judge to grade FRQs.**

## Implementation

### Benchmark Dataset Construction

MT-Bench provides 80 free-response questions. However, these questions are not primarily tutoring scenarios, so TutorBench will supply the main pedagogical question bank.

We’ll filter TutorBench for the text-based scenarios, since image-based, multimodal scenarios add a layer of complexity that we plan to address after initial validation. We’ll primarily focus on Content, Diagnosis, and Scaffolding, ignoring Adaptation on TutorBench for now. 

Unlike TutorBench, IRT weights won’t be negative, and there will be a separate critical failure flag. Instead of “tutor revealing final answer”  being a \-5, we can have “tutor avoiding final answer reveal” being a positive 1\. This fits the IRT model better since it associates higher ability to increase probability of better outcomes. Additionally, it allows a separate evaluation of critical failures so they aren’t compensated for by otherwise good performance. 

Our idea involves a preprocessing step on the TutorBench dataset, which helps evaluate the LLM tutor’s performance. We split each element in the dataset into a scenario schema and rubric schema(s) (see “Schemas”). The scenario schema tracks information like the prompt (the question and the student’s perspective) and the reference solution. The reference solution is broken down into a set of criteria, and each criterion has a corresponding rubric schema, tracking the criterion itself, “evidence” (what should happen to fulfill this criterion), and q-mapping/discrimination (which skills (Content, Diagnosis, Scaffolding) does this criterion cover).

### CAT Integration

After preprocessing and calibration, we start with the LLM tutor answering a scenario. The LLM judge evaluates the LLM tutor’s output, where each criterion gets a pass/fail (using the corresponding scenario/rubric schemas). This evaluation is conveyed with a result schema. The pass/fail verdict (along with the criterion’s calibrated discrimination, difficulty, and q-matrix) is used to update the MIRT running vector, which tracks the competency level of the LLM tutor in each skill. After this, we calculate the standard error for each skill’s competency level (conceptually, this is how uncertain the LLM judge is about each skill’s ability value), and then select the skill with the highest standard error. This skill will drive the CAT (computerized adaptive testing) mechanism that chooses the next scenario (see “Choosing Next Scenario”). Choosing the skill corresponding to the highest standard error skill ensures efficient evaluation, since we’re targeting the model’s most uncertain points. After the next scenario is chosen, this cycle repeats.

**Choosing Next Scenario:**  
We compute the fisher information averaged over the criteria with the target skill in a given scenario. This value is calculated for all scenarios that the model has not been evaluated on and that contain at least 1 criterion that maps to the target skill. The top-n scenarios are gathered, and the selected scenario is randomly chosen from these top-n.

**Stopping Rule:**  
When every skill has less than the maximum standard error allowed, and every skill has at least 15 evaluations, the CAT evaluation finishes. 

EVERYTHING UNDER IS INCOMPLETE \- UNDER CONSTRUCTION FOR AFTER THURSDAY

### Calibration Workflow

Based on the FLUID workflow:  
Given:

* Dataset (TutorBench)  
* 

Calibration Schema  
**MIRT item Parameter Schema:**  
{  
  "criterion\_id": "tb\_001\_c01",  
  "difficulty": 0.42,  
  "discrimination": {  
    "content": 0.0,  
    "diagnosis": 1.18,  
    "scaffolding": 0.0,  
    "adaptation": 0.0  
  },  
  "fit\_statistics": {  
    "infit\_mnsq": 1.08,  
    "outfit\_mnsq": 1.21  
  },  
  "calibration\_sample\_size": 240,  
  "calibration\_version": "mirt-v1"  
}

Start with TutorBench dataset

### Judging workflow:

Criterions will be given in batches, grouped by skill, so LLM judges aren’t overwhelmed but still efficient.   
One judge will score all of the criterion batches, then a second judge will go through and reevaluate critical failures, disagreements in repeated runs, a random sample of scenarios and more such cases.

**Judge choice:**   
We may choose to have a human evaluate a small sample as well, to get a proper reference on judge agreement with a human.   
Currently undecided, considering Prometheus 2 as an option or a more general frontier model potentially. Judges will be validated using accuracy, F1, agreement, and false-positive/negative rates, with stricter requirements for critical failures. We will also test consistency across repeated runs and minor changes to prompts in formatting, using GRM-based reliability analysis to identify unstable judges. 

These factors will all be taken into account when deciding a judge llm, which is then frozen to run and score responses from EduLLM/other competitors and produce skill estimates. 

**Steps to prep for Judge Choice Workflow**:	

- We will use three tutor models (potentially GPT-5.5, Opus 4.8, Gemini 3.5 Flash) to get sample responses for the potential judge LLMs to evaluate.  
- From our preprocessed data, we will randomly select 10 Scenario Schemas to prompt the tutor models with. We will also take the Rubric Schemas associated with these scenarios to use when grading.  
- We will human grade each of the tutor generated responses based on the Rubric Schemas associated with each scenario (i.e. determine whether this response should be a pass or a fail for each criteria).

**Potential judge acceptance criteria:\\**  
F1 \- How well a judge detects failures while being accurate  
Macro-F1 \>= 0.80 overall \- F1 based on amount of criteria  
Sensitivity \>= 0.90 for critical failures \- Must detect at least that many critical failures  
Test-retest agreement \>= 0.90 \- Repeated judgements of same material must produce same response 90% of the time  
No skill category with F1 below 0.70 \-   
Prompt-consistency \<= 0.1 \- variation in model response to tiny prompt changes under .1  
Marginal reliability \>= 0.7  \- how much the judge uses actual signal to distinguish response quality

Five-Candidate Shortlist

| Candidate | Category | Access | Price/size | Why |
| :---- | :---- | :---- | :---- | :---- |
| Prometheus 2 7B | Specialist Judge | Self-host | 7B; local GPU | Judge-specific training in a size realistic for repeated checkpoint use |
| GPT-5.6 Luna | Efficient API Generalist | OpenAI API | $1/$6 | Current GPT5.6 family at its cost-sensitive, high-volume tier |
| Claude Haiku 4.5 | Efficient API Generalist | Claude API/clouds | $1/$5 | Anthropic’s fastest near-frontier model and an independent judge family |
| Gemini 3.5 Flash-Lite | Efficient API Generalist | Gemini API | $0.30/$2.50 | Stable Google model explicitly optimized for low latency and high volume |
| Qwen3.5-9B | Open generalist | Self-host/provider | 9B; local GPU | Modern open control candidate without the cost of a 27B model |

* Prometheus tests whether judge-specific training data can let a 7B model compete with newer general modes  
* GPT, Claude, and Gemini test three independently hosted API families built for fast, high-volume use. It’s not their largest and most expensive flagships  
* Qwen tests whether a modern 9B open model can be prompted or later tuned into a reliable local judge  
* 

### Code Implementation Details:

FastChat is a code framework that sends these questions to the LLM being judged, and sends the responses to the actual LLM judge, providing it with rubrics, what to evaluateonetc.FastChatis like the intermediary.

![][image1]

Schemas  
**Scenario Schema:**   
{  
  "scenario\_id": "tb\_001",  
  "use\_case": "assessment\_feedback",  \- TutorBench’s label for the problem  
  "subject": "mathematics",  
  "grade\_band": "6-8",  
  "modality": "text", \- For now all of these will be text until image/multimodal is added  
  "prompt": "A student claims that 3/5 \+ 1/5 \= 4/10...", \- request for the actual tutor LLM  
  "conversation\_context": \[\],   
  "reference\_solution": "The denominator remains five...", \- what the LLM should generally be answering with  
  "criterion\_ids": \["tb\_001\_c01", "tb\_001\_c02"\], \- link to specific criteria entries used for evaluation,  
  "source": "TutorBench", \- which dataset it came from  
  "split": "calibration", \- which training portion this is part of  
  "version": "1.0"  
}

**Rubric Schema:**  
{  
  "criterion\_id": "tb\_001\_c01",  
  "scenario\_id": "tb\_001",  
  "criterion": "The response identifies that the denominator remains five.", \- atomized metric/criteria  
  "expected\_evidence": \[   
    "Fractions with a common denominator retain that denominator.",  
    "3/5 \+ 1/5 \= 4/5."  
  \], \- supporting context for the criterion/what’s expected in the response  
  "scoring\_type": "binary", \- no polytumous for now  
  "score\_anchors": null, \- if polytumous, specific definitions for each level in the score range  
  "primary\_skill": "diagnosis",   
  "q\_mapping": {      
    "content": 0,  
    "diagnosis": 1,  
    "scaffolding": 0,  
    "adaptation": 0  
  }, \- MIRT skill mapping  
  "q\_rationale": "The criterion requires identifying the student's specific misconception.",   
  "criticality": "critical", \- if it fails here, it’s a major error   
  "objectivity": "objective", \- if it’s externally verifiable  
  "explicitness": "explicit", \- if the criteria is explicitly asked for in the scenario prompt  
  "source": "TutorBench",  
  "status": "approved", \- if the criteria is finalized/approved  
  "version": "1.0"  
}

**Judge Result Schema:**  
{  
  "run\_id": "run\_20260721\_001",  
  "candidate\_model": "edullm-checkpoint-1200",  
  "scenario\_id": "tb\_001",  
  "criterion\_id": "tb\_001\_c01",  
  "candidate\_response": "...",  
  "judge\_model": "prometheus-2",  
  "judge\_prompt\_version": "judge-v3", \- which judging instructions being used  
  "verdict": "pass",  
  "score": 1,  
  "evidence": "The response states that the common denominator stays five.",  
  "rationale": "This directly corrects the identified misconception.",  
  "unscorable\_reason": null,  
  "seed": 42  
}

**Running MIRT Ability Estimate Schema:**  
{  
  "run\_id": "run\_20260721\_001",  
  "candidate\_model": "edullm-checkpoint-1200",  
  "ability\_estimates": {  
    "content": {  
      "theta": 0.91,  
      "standard\_error": 0.18  
    },  
    "diagnosis": {  
      "theta": 0.64,  
      "standard\_error": 0.23  
    },  
    "scaffolding": {  
      "theta": \-0.12,  
      "standard\_error": 0.29  
    }  
  },  
  "scenarios\_administered": 47,  
  "critical\_failures": \[  
    {  
      "scenario\_id": "tb\_019",  
      "criterion\_id": "tb\_019\_c04",  
      "type": "answer\_leakage"  
    }  
  \],  
  "calibration\_version": "mirt-v1",  
  "q\_matrix\_version": "q-v2"  
}

**Calibration Monitoring Schema:**  
{  
  "criterion\_id": "tb\_001\_c01",  
  "monitoring\_window": "2026-Q4",  
  "new\_models\_observed": 35,  
  "expected\_pass\_rate": 0.61, \- MIRT expected value  
  "observed\_pass\_rate": 0.48,   
  "average\_raw\_residual": \-0.13, \- difference  
  "residual\_ci\_lower": \-0.29,  
  "residual\_ci\_upper": 0.03,  
  "drift\_status": "watch", \- whether the criterion MIRT   
  "practical\_threshold\_exceeded": true,  
  "statistical\_threshold\_exceeded": false,  
  "judge\_version": "judge-v3",  
  "calibration\_version": "mirt-v1"  
}  
{  
  "criterion\_id": "tb\_001\_c01",  
  "monitoring\_window": "2026-Q4",  
  "new\_models\_observed": 35,   
  "expected\_pass\_rate": 0.61, \- MIRT expected value  
  "observed\_pass\_rate": 0.48, \-  
  "average\_residual": \-0.13, \- difference  
  "drift\_flag": true, \- large residual given sufficient sample size  
  "judge\_version": "judge-v3",   
  "calibration\_version": "mirt-v1"  
}  


[image1]: <data:image/png;base64,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QggFEeoTIoR+IkIIBRHqEyKEfiJCCAUR6hMihH4iQggFEeoTIoR+IkIIBRHqEyKEfiJCCAUR6hMihH4iQggFEeoTIoR+IkIIBRHqk25GaNiwYdnTTz/d8bjs27evmD516lR23nnnlZYxb775Zr4uf3wwZ3ObJtHfZ/HixaXxbiBCCAUR6pOQInT8+PFs6tSpxfx99903YDD279+f/frXvy6ND2agdYIIoZmIUJ+EFKEXXnihJRCabhcMPe6ZM2fmbMyd/vnnn1vmd+zYkV144YXZlClTWtZ55513ZmPGjMleeumllvFt27ZlF1xwQTZt2rTsxx9/LN2/6PdwtIz21j766KNifPny5fn4jBkzirGvv/46v/+LL74427t3bzGux3jkyJE8vnpsGjt06FA2ffr0fB2ffvppseyDDz6YP9Ybb7wx30v0H4/MmTMnGz9+fPboo48WYz/88EO+vrFjx+Z/Y3f5Xbt2ZZdddll2zjnnZCtWrMjHLEKzZs3KH4Men38/dSFCCAUR6pOQIqQNnq7TRtiWkxMnTpSW/fLLL/ONsh8tm965c2cx/9133+XTV199dfbEE08U49ooa3r16tX55ejRo/Nxm3/88cez+++/P5/euHFj6TFofNKkSdlTTz2VnX/++fnYueeem48vXLgwD5zG1q1bl48988wzxfoUMFuHKA5r1qzJA6V53VbLa/r5558vIrl06dL8cfuPxX08FnN7PrrUc7/iiivyca1L47/73e/yef06rN2X+5gWLFiQjRw5shjvBiKEUBChPgkpQhrXv9gvueSS7K233io2htrw+su6t6madiOkS22A/eV0qQ2/pt33l3T57rvvFstr72LUqFGV9z179uzS2MGDB0tjWofNa29m4sSJxXW7d+9uWfbuu+8u5l955ZV8bNGiRfnlsWPHWtbt389A89obGzduXL6nZNc/9NBDletxD8f566kTEUIoiFCfhBYh7QXpcsSIEcUhIR0q8pd1b1M17Udo+/btpeV0aYfM3MN0uvTp8fj3bfchW7duzQ/5uY/Bvb/33nuvmL/55ptb7stftoqusz1FC5jPv43dTs9R04rP8OHDi+eiMb0PV7UeP0LtDv8NFRFCKIhQn4QWIbu0aTs05i/r38af9iP02muvlZbTXoXdl9gn8zT9wQcflO6rHR3Ocu/Lv95/voqIHVLzl9f8b37zm9I6jA5Nahkd7vOv89fljltsbrnllpYI2Z6gvzwRQtMQoT7pdoS0J6P3IIxt+P3x999/v9iI6s16d4PabuPqX6dpfVpOHyrQtF2nw3ua3rBhQx4aG9fG+JFHHslefPHF7LHHHssOHz6cj1933XX5Mlu2bMn3zHT4zL9f0V6F3th/8skni3XqUuP6YMTmzZvzMb23pHGt64svvsinv/nmm9LjF8VSY4qgwqEPM2hce1l6bvqQgK5X+PzHo3E7lKfHZB+AcJ+L+3fRa6DpTZs25dfZIUeNESE0DRHqk25HyKf3S/wx0QbA/oWuja/eA3HXU7UR1JhtUEWf7tK8ImAbe7tO7zW596cxC58O9+nNe3f5u+66q1j28ssvL923KBC2jPbYbFx7OjZuH0DQBw1sbOXKlcWy7n0a+xCCTJ48OR/Tp+9szN7T8bmHBxXOPXv25OP2YQz9XSxEdpvbb7+9uI1ibY/J/RSd5qv+/nUgQggFEeqTbkao22wD6o93au3atcUn8UTrqvokHrqHCCEURKhPYo2Q/evdDnmdDX2/x9Yj9j0d9A4RQiiIUJ/EGiGkgQghFESoT4gQ+okIIRREqE/+4z/+o7RhiNkbb7zR8ftER48e7XjZs1HXuu2MC/54CogQQkGE+qTJERJtePyxulx//fX5pf+JtDOlTwvedtttpfEUECGEggj1SQwR0kepde6zV199Nf/o8eeff55/l+jkyZPFMnayUouQNig6j5t7pgIto+/EKA76CLJ/AlS54YYb8vuYP39+6XEY9wSl+v6Ords9Eam7bp0gVI9J8/rot61HpyPSaXSuuuqqYqzdc3UfZ7uToerj4Dq5qT7CvX79+tLjDhERQiiIUJ/EEKHnnnsu34jbl0lF8+4pZ2xPw98T0nePbN4+BWfX6fY2b+dms7C5H912aRn3PHDuyVbdmNiYLePvCWl+2bJlLfM6KWvVc9V57Oz29ok+93b33HNPPu2e307j+sKse58hIkIIBRHqk5gi5I5pvpMIuWdI0KV7MlQ3Qvoip79XVMV/HAON21i7COnUPUbz9957b+VzdSOkS53U1K7TiVn12DXtPn4tV3VKntAQIYSCCPVJ6hFyQ6NLHUprd50OZfn37bKzPfjjdvt2Y36EbF6H71w6K0HVc/Uj5J5xQWfxtuuI0NAQoWYjQn0Sc4SWLFmST+unEOx6P0J2Hji7TbsI2Zmm/fv2+Rt3O3xXdVsbc+/Hvc59v8pUPVc3QtrrmTBhQst6rrzyynyaCA0NEWo2ItQnsUbIPxecXW8Rcn3//ff5dZpuFyG73qUPBfiPRWfD9pez2/rLtlu35hXQqvVUPVc3Qvaxcv92QoSGhgg1GxHqkxgi1I4OYdmn04w+LaYNtd7Yb/ez3APRbb/66qvSuE8nSG334YV2dK46rd8d06fY3E/5dUrP+0zvP0RECKEgQn0Sc4QQPyKEUBChPiFC6CcihFAQoT4hQugnIoRQEKE+IULoJyKEUBChPiFC6CcihFAQoT4hQugnIoRQEKE+IULoJyKEUBChPiFC6CcihFAQoT4hQugnIoRQEKE+IULoJyKEUBChPlm1alVpwwD0ChFCKIhQnxAh9BMRQiiIUJ+sXr06e/vtt3GWnnjiidIYzoz/32S/EKFmI0KIEhuudPBaNhsRQpTYcKWD17LZiBCixIYrHbyWzUaEECU2XOngtWw2IoQoseFKB69lsxEhRIkNVzp4LZuNCCFKbLjSwWvZbEQIUWLDlQ5ey2YjQogSG6508Fo2GxFClNhwpYPXstmIEKLEhisdvJbNRoQQJTZc6eC1bDYihCix4UoHr2WzESFEiQ1XOngtm40IIUpsuNLBa9lsRAhRYsOVDl7LZiNCiBIbrnTwWjYbEUKU2HClg9ey2YgQosSGKx28ls1GhBAlNlzp4LVsNiKEKLHhSgevZbMRIUSJDVc6eC2bjQghSmy40sFr2WxECFFiw5UOXstmI0KIEhuudPBaNhsRQpTYcKWD17LZiBCixIYrHbyWzUaEECU2XOngtWw2IoQoseFKB69lsxEhRIkNVzp4LZuNCCEq2mD5VqxYUVoO8SBCzUaEEBU/QGzA4sdr2GxECFHZsmULe0GJIULNRoQQHfaC0sLr2GxECNGxvSH2gtJAhJqNCNXsnXfeQQ889thjpTHUb926daX/xutGhJqNCNXspZdeyk6fPg0kYe3ataX/xutGhJqNCNWMCCElRAjdRoRqRoSQEiKEbiNCNSNCSAkRQrcRoZoRIaTkv/7rv0r/jdeNCDUbEaoZEUJKiBC6jQjVjAghJUQI3UaEakaEkBIihG4jQjUjQkgJEUK3EaGaESGkhAih24hQzYgQUkKE0G1EqGZECCkhQug2IlQzIoSUECF0GxGqGRFCSogQuo0I1SzGCP3888/Z9u3bi/lHHnkkmzp1ajE/bNiw7I033iimfe3G5fLLLy/d31BdcMEF2YUXXlgad91xxx3Z5s2bS+N1seft0/gnn3xSWuayyy7Lxo8fX1o+dHou/n/jdSNCzUaEahZjhJ566qnsnHPOKebduNi8G6Hbb7+95bqFCxcW87feemvbDXRdOomQHsPGjRtL43Xp5DkSoc4QoWYjQjWLMULaOPrR8ecHitCTTz5ZzA8WoenTp2fnnntuMT9mzJhs9uzZLfdrli5dWiyn27jXWYT825w6dao0busYNWpUaVlxx7RX6D/m4cOHF9dPnDixWL9db9P+/bnTRKg9ItRsRKhmMUbINp47duzI3nvvvcqNqRshbUxvvvnmYuPsrmuoEbLxt956q5h/9tln82kLzNixY4sIffDBB8VttMzFF19cTLt7QldccUXL+seNG5c/fgXVxo8fP15c79L1J0+ezKd3795djNmlezv/72bTRKg9ItRsRKhmsUZozpw5+WGu0aNHZw899FA+5obHnVY4dPjO3ciauiJk8/v27csv77rrrmLcPRynMNmenIwYMaK4rRshu04BE01r7MiRI/mlnvdAEZLf/e53LWNaT9VjrpomQu0RoWYjQjWLNUL6l75tbDWmWEyePLm4vupwnG2I3XXVHaHvv/8+v3zmmWeKcTdCuu7SSy/Np2+55ZYBI/Tqq6/m0TFHjx4trtdhNi3zww8/tDwGs2zZsvx6C4mmtScltodm41XTRKg9ItRsRKhmsUVoxYoVxcZSlzb95ptvtoxXRejgwYP5/DfffFOsb7AIXXfddaUNdVWENm3aVMyPHDmy5To/QjY+a9asPAo2bo9ZFCe7rh3dZsaMGaVx20NauXJly99El4q3u17/uWlPTtNXX331gH+XUBEhdBsRqllsEdJG1/Zmbrvttmz+/Pn5tL1hr2l3g65p94MJF110UcvGdbAIHTp0KL/e1e6DCXPnzs3HFQH/uqoPJrjvUel52fjvf//70rLy008/5R9Hd8f2799feszu9e7eoXv9qlWrKsdt/quvvsqn9eEIf/0hI0LoNiJUs9gi1C/62Wh/zDbYVdeJvsukw2j++Pr164tDYvpwhY3v2rWrdHhNeyYad8e0znb36d6HAuqPD8a9Lz3GLVu2lJYJGRFCtxGhmhGhs+fuRSAMRAjdRoRqRoTOnj6k4I+hv4gQuo0I1YwIISVECN1GhGoWeoTc90xS8cADD2T33HNPaTwGq1evLo2FhAih24hQzUKJ0J49e/KPNrtjN9xwQ23vu+h8c88//3xpvB86OZdcL02ZMqU01o5eD/f7SlWqXsteIULoNiJUs1Ai5H6npRu0bvcLpP0UWoTq/rt3+7UcCBFCtxGhmnUzQjqUZt89EfvOig5FueMac+e1B/Tpp5/m0/bFSn03SP9it2X0jX6btrMO6GPL7nrs5x30XSL//kQxcMf37t1beiz+c/Kvd5fR9CWXXFKM2/OVdic09ddr3xGy+e+++y7/zpOdtse/z507d1Y+no8//rhlTH9TjevvprNAaGzatGmVt/Xvyz3/nC2jv5V/W385OXHiRHEb93l16zArEUK3EaGadTNC2tjo9DHu/JdffplfPvroo/mYvhSpy6p/Pd99990tEbJDPPrSpo3budTsNvY7Q7p0xzXt7gktWrSo5fprr702n696HL5XXnmlZb1ffPFFMX3vvffm01u3bi3WM9AJTV1apl2E/Ody0003FdM6j55dZ98N0ridBsj+Rrp/Reiqq64q3a87/+233xbTOkdd1RkWLEKHDx/O5/WlVjvNj/831LS+ZKvpDRs2lO6vTkQI3UaEatbtCNkJOO3kmdpI2xmiFRlb1t9wiR+hmTNnFtfZTxTY/dj066+/ns8bdxk3QnYeNf/x2bK6zvaMfD/++GNxtgNRZOx2io//uHTZ7oSmLi3XSYTOP//8fB3ufbg+//zzfNx/bu+//34eIX/5qnW4e53u9TZtEbLxX/3qV8W8/1rqvzGbV9T0+vv3VxcihG4jQjXrVoTsdDfuCTjF9gb0TXx3A+dvuORMI/Twww/n0/atf3/j6UZI8zovnP/47Hr/pxSM/uWvcf10g63HfiRP0+0i1O6Epi4t10mEFAitw86F56/H/pZVz62TCGlePx2hafdcfe6yfoTsTObu/fvrtD0yHaJzr6sTEUK3EaGadStCog2Ofu/HH3c39raxst8Fcpc70whpWfdf2f7Gc968ecW8vXfj3p9vwoQJpbHHH3+8tF4LjKarIjTQCU1dWubKK6/Mp+0s4QNFyG6zZs2a4jrdzs6jp0+p+ffRaYRsWj/U5z9fXQ4UoarXUn9L91x53UKE0G1EqGbdjNCSJUvyjY5L41Vj/rjmzzRCdtjHNvpy44035tc9/fTTxZgdGvMfh07q6X+4wX9Ox44dK93OHqOmqyI00AlNXU888URpucEi5D4vo3GdZLVqvF2E3GVs2j3kaB/LtmUGipC7Dtuzs99Z0nL+/deJCKHbiFDNuhkho5Np2i99Gn1wwD9Zp3z22WdDOlyjjaX7xrr7sw06ROhGQrRXtm3btpYxRUOfzvMfs2vdunXFtN5s96+v0u6Epi49d/0shD8+GH04ouqEpfp7+mNV9IERHd6z+c2bNxc/CVH1E+Kd8F9fN1LdQoTQbUSoZr2IEJrNfn5CH0rwr6sbEUK3EaGaESF0mz7NZ79f1G1ECN1GhGpGhJASIoRuI0I1I0JISS8itGDBgtIYmoMI1YwIISVECN1GhGpGhJCSXkRIX+D1x9AcRKhmRAgpIULoNiJUMyKElHQ7QjrdlPjjaA4iVDMihJQQIXQbEaoZEUJKdNol/7/xOnEoDkSoZkQIKSFC6DYiVDNFSD9PAKSg2xHi49kgQjWzY9zoLp252x9Dd/j/jdeJsyWACCFKbLzSwOsIIoQosfFKA68jiBCixMYrDbwnBCKEKLHxSgOfjgMRQpSIUPwIEIQIIUpEKH68hhAihCjxr+j48b4ehAghSr34Dgu6iwhBiBCixeGcuLE3CyFCiBb/ko4be7IQIoRoESEgfkQI0eJwHBA/IoRo8eGEePF+EAwRQtTYG4oTh1JhiBCixsYsTrxuMEQIUWNPKE4cjoMhQogeIYoLAYKLCCF6HNqJC68XXEQI0WNPKC68XnARISSBDVscOBQHHxFCEjjEEwdeJ/iIEJLAF1fjwB4rfEQIyeBf2WHjUByqECEkg39lh41/JKAKEUJSCFG42BNCFSKEpPCv7TARILRDhJAUfTiBDV54+McB2iFCSA4bvLDwDwMMhAghOXxUOyz8owADIUJIEiEKBxHCQIgQksSGLwwchsNgiBCSxBkUwsA/BjAYIoRksQHsL+0FsSeEwRAhJIu9of7iHwHoBBFC0tgQ9g97QegEEULyYj2Vz549e7LFixdnq1atKl0XOuKPThEhJC/GCFmATp8+ne3YsSOqEPFeEM4EEUIjxBQiN0BGIfKXCxV7QTgTRAiNEEuEqgJkNO4vHxr2gnCmiBAaI/R/oQ8UoFhCFPrfGOEhQmiMkPeGOglQ6CFiLwhngwihUUL8l/qZBCjkEIX4t0X4iBAaRf9SD+kLrGcToBBDxF4QzhYRQuOE8i/2oQQotBCF8jdFfIgQGieE0/nUESDT7xCxF4ShIEJopH7+y73OAJl+hqiff0vEjwihkfr1k9PdCJDpR4jYC8JQESE0Vq//Bd/NAJleh6jXf0Okhwih0Xr13aFeBMj0KkTsAaEORAiN1osI9TJAphchYi8IdSBCaLxubkz7ESDTzRCxF4S6ECE0nvaGuvGR7X4GyHQrRN0MN5qFCAF/qH+jGkKATN0hqvtvhWYjQsAf6v0Ca0gBMnWGiENxqBMRAv6mjn/hhxggU0eI6vgbAS4iBPzNUL/AGnKAzFBCNNS/D1CFCAGOs/2XfgwBMmcborP92wADIUKA50y/OxRTgMyZhojT86BbiBDgOdMIxRYgcyYhYi8I3UKEgAruRnegKMUaINNJiNgDQjcRIaCCNryKj2LUbi9AG/AdO3aUNuyxGSxE7Z4/UAciBHj0KTCLT7sIpRIg0y5EVc8dqBMRAhxVAfI3xKkFyFSFiENx6DYiBFRoF6FUA2TcEPnxBbqBCAFtaC/AjVDqATKrVq3iI9noGSIEDEIRakqARM/zscceK/0dgG4gQsAgmhQgo+erPSL/bwHUjQgBA2higAwhQi8QIaCNJgfIECJ0GxECKhCg/0OI0E1ECHDEeDLSXqn6HhEwVEQI+BsCNDhChLoRIeAPBOhMECLUiQih8QjQmSNEqAsRQqMRoLNHiFAHIoTGIkBDR4gwVEQIjUSA6kOIMBRECI1DgOpHiHC2iBAahQB1DyHC2SBCaAwC1H2ECGeKCKERCFDvECKcCSKE5BGg3iNE6BQRQtIIUP8QInSCCCFZBKj/CBEGQ4SQJAIUDkKEgRAhJIkAhYUQoR0ihOQQoDARIlQhQkiKNnT8Imq4CBF8RAjJIEBxIERwESEkgQDFhRDBECFEjwDFadWqVaXXEs1DhBA1AhQvvW6ECEQI0SJA8SNEIEKIEgFKByFqNiKE6BCg9BCi5iJCiAoBShchaiYihGgQoPQRouYhQogCAWoOQtQsRAjBI0DNQ4iagwghaASouQhRMxAhBIsAgRCljwghSAQIhhCljQghOAQIPkKULiKEoBAgtEOI0kSEEAwChMEQovQQIfTdnj178gD5GxygHf334v93hDgRIfQVAcLZIkRpIELoGwKEoSJE8SNC6AsChLoQorgRIfTcUAP0xRdflPjLdMtFF11UGuu277//Prv00kvz6ePHj2eHDh0qLSN6bJs2bSqNx2Coj50QxYsIoaeGGiAZNmxYib9Mt/TyvkTR0X3OmTMnnx83blw2fPjw0nKi5T7++OPSeAzqeOyEKE5ECD011ABJP0Jg072+74cffrjj+6xjQ94vdT12QhQfIoSeqSNA0m6jrPFLLrmktHe0du3alr2mZ555Jh+fPn16du655xbLjRkzJps9e3Y+PWLEiJbbPPLII8V9uL788svS4xDtrbjLaUx7ge7Y1VdfnY/PmzcvmzRpUml5/b3csQULFuSX06ZNK+7HvV5sQ75o0aKW8RdeeKFYXntVNq7naeu66667Wm6jMX0vxx2bPHly6blWPY5Tp07l42PHjs0uv/zy0nqrblNHhIQQxYUIoSe0Yajri6j+xssdv/fee4vpm266qZi2jfCPP/5Y3KZdhNasWVNa70cffVRMb9u2LZ8+//zzW5Yzo0ePbjlktnPnzuK2Fp5jx47l8/v27csj5N/f8uXL8+nrrrsuGzVqVHGdYmUR0rgbEXucR44cyacPHjyYj3/zzTfF+nU5ZcqUfPrkyZPF+MaNG/Pp/fv35/O7d+8ull+2bFnLfVSF94MPPmhZ5uKLL86nFSH38eu6n3/+OX+MVY/dX+/ZIkTxIELoujoDJFUbfhvfunVrPq1AXHDBBdmJEydKy9uGtF2E3nrrrZbbaPrNN98s3fcnn3xSWrf/OPxxf/6pp57KIzRy5MiW8fvvvz+fHihCWu7o0aMtt9PexBNPPJFPKwBG8/a3cPdI7TFpne5jcK/312Ohd2nPZ/z48fn1YoHRbWbOnNmyPkX+lltuqXzs/nqHghDFgQihq+oOkPgbc3fcNv76174idPjw4dLytsFrFyFbRof27PBe1X1/9tlnpXXbMrZH4Y/78/Pnzx9ShPz16Xnddttt+bT2Nly2TFWEJkyYkEfEXZ8+hVe1HjvU5t+3fYJPgRksQrY35o7XHSEhROEjQuiabgRI/I2vO+5HyMb1foym7VCVprWBd9elaYuQNp76JNrcuXNL92HTA0XI7lt02MvGV6xY0bLcli1bhhShm2++ueV22pDbobV2saiKkPbI3OfiPub33nuvtB6fe9tZs2YVhyPbRUjvWVU9dn+9deBcc2EjQuiKbgVItMHy2XhVhG699db8Ovuwgf0r3P6l73L3hFz2Pojdl7SL0N69e0u317gO6WnaPrSg9440frYR0vtG/v3Y+yoKqDs+ceLEYt1VEZKqD1MsWbKkdB+2vMu93taj8XYR8m8jdb4n5OKkp2EjQqhdNwM0FPqUnD820LixT3f544PR30AfhPDH9eXaqr2Us7V+/frSmBnsufm0B6SI+uO6D9s7akfL2PPq9PUf6LHXiRCFiwihVqEG6EzYITt9lNk+zqz3OfzlEBdCFCYihNq5h3tipUNGOuylQ3rvvvtu6XrEhw8phIkIoStSCBHSQYDCRYTQNYQoXKtXr85PCbRr167SdakhQGEjQugqQhQmfYJN35HS+12ff/556fpUrFu3rvTfJMJChNB1hKi33BOuDkYRuuGGG0rjKVCA+CBC+IgQeiLVEOmsC+53XTT20ksvtYzpQw7bt29v+Zi3nTtO0+1OEvrcc8+1jPsnYp06dWqxvnYnXL3++utL67Db6BxuGtP56/znFTsCFA8ihJ5JLUR2pmqbtx+b05hOGqppnSHBltGlnQxUX5jVF0r9cZvXl2MtQjr1kF2nmNmlrbfdCVf9MydYHN3lli5dWsynggDFhQihp2L/DpFL76u4ZwOQDz/8sGVDLzb/+OOPtwTJ4qLpqpOEWoTcdb3++uv5mNFYuxOu2l6av24tY3tV7rpTQIDiQ4TQU9pApBIibcRvvPHGljE9P3/jrnnbG9G0zsVmywx0klA/QvYDd/aJNj88/glXdaoehcdft67TWRF00lL3ccaOAMWJCKHnUgnRjBkzWkKg09rYz3HbmDaM7rz9eJ0+Im1jFiZ//X6EtOd1xRVXtNzOpqtOuLpw4cKWZVyK3JNPPlkajxUBihcRQl+kEiJt5F0as9DYiTz1cw22vB8paXeSUD9C9p6OTnZqy9memH97O+Gqf1LS22+/PR/X7TR/4MCB0nOKDQGKGxFC36QSIp2Q1D6U4Gp38tBNmzaVxqSTk4Tqh+C+/fbbYt4+AOHyT7iqH7PThxT85apuGxsCFD8ihL5KJUT91NQTrhKgNBAh9B0hGrqmnXCVAKWDCCEIhAidIkBpIUIIBiHCYAhQeogQgkKI0A4BShMRQnAIEXwEKF1ECEEiRDAEKG1ECMEiRCBA6SNCCF5qZ99GZ/hF1GYgQogCIWoWAtQcRAjRIETNQICahQghKoQobQSoeYgQokOI0kSAmokIIUqEKC0EqLmIEKJFiNJAgJqNCCFqhChuBAhECNEjRHEiQBAihCQQorjoTAj+a4hmIkJIBiGKA6figYsIISmEKGwECD4ihOQQojARIFQhQkgSZ98OCwFCO0QISeJnIMJBgDAQIoRkEaL+I0AYDBFC0ghR/xAgdIIIIXmEqPcIEDpFhNAIhKh3CBDOBBFCYxCi7iNAOFNECI1CiLqHAOFsECE0DiGqHwHC2SJCaCRCVB8ChKEgQmgsQjR0BAhDRYTQaITo7BEg1IEIofEI0ZkjQKgLEQL+QIjOBAFCnYgQ8DeEaHAECHUjQoCDELVHgNANRAjwEKIyAoRuIUJABUL0fwgQuokIAW0QIgKE7iNCwACaHCIChF4gQsAgmhgiAoReIUJAB5oUIgKEXiJCwBlYvHhxaaOdEgXIf85ANxEh4AylGiL2gNAPRAg4C6mFiAChX4gQcJZSCREBQj8RIWAIYg8RAUK/ESFgiGL91BwBQgiIEDBEMX58mwAhFEQIqEFMISJACAkRAmoSQ4gIEEJDhIAahRwiAoQQESGgZiGGiAAhVEQI6IKQQkSAEDIiBHRJCCEiQAgdEQK6qJ8hIkCIARECuqwfISJAiAURAnqglyEiQIgJEQJ6pBchIkCIDRECeqibISJAiBERAnqsGyEiQIgVEQL6oM4QESDEjAgBfVJHiAgQYkeEgD4aSogIEFJAhIA+O5sQESCkgggBATiTEBEgpIQIAYHoJEQECKkhQkBABgoRAUKKiBAQmHYhIkBIERECAuSHaPHixaVlgBQQISBQFiIChJQRISBgjz32WGkMSAkRAgJ2zTXXlMaAlBAhIGBECKkjQkDAiBBSR4SAgBEhpI4IAQEjQkgdEQICRoSQOiIEBIwIIXVECAgYEULqiBAQMCKE1BEhIGBECKkjQkDAiBBSR4SAgBEhpI4IAQEjQkgdEQICRoSQOiIEBIwIIXVECAgYEULqiBDQY3/84x87dsstt5TGXP66gdgQIaDH/vKXv3TsnXfeKY25/HUDsSFCQI/5IRkIEULqiBDQY35IBkKEkDoiBPSY4nHq1Klsy5Ytpaj4Y0QIqSNCQI9ZQIYNG5bt2LGjmJ81a1Y+RoTQJEQI6DELyEMPPVRE53//93/z6ePHjxMhNAoRAnrMjYjCs2TJkuwXv/hFNmHChHzs3//93/Nxs2nTpmJZQ4SQCiIE9JgboW3btrWEZfv27fm09ow0f+eddxbX/f3f/31+efToUSKEZBAhoMfcCNkezrx58/LpGTNm5PP/+I//mBs1alQRIV3+y7/8S8tt/XUDsSFCQI9VRejuu+/Op//pn/4pj8///M//5P7zP/8zv9R1f/rTn/LDdhyOQ0qIENBjA0Xot7/9bUtkqj6YoOuff/55IoQkECGgx6qiYhESvfdj7xPJvffeWyxnbFl/3UBsiBDQY36Eqvz5z3/OP6Tg7gnp49sac5fz1w3EhggBPeYHZyBVh+Nc/rqB2BAhoMf8kAyECCF1RAjoMT8kAyFCSB0RAgI2d+7c0hiQEiIEBIyf90bqiBAQMCKE1BEhIGBECKkjQkDAiBBSR4SAgBEhpI4IAQEjQkgdEQICRoSQOiIEBIwIIXVECAgYEULqiBAQMCKE1BEhIGBECKkjQkDAiBBSR4SAwCxYsCCPj89fDkgBEQIC5AeICCFVRAgIkB+gFStWlJYBUkCEgECxF4QmIEJAoNgLQhMQIXTshx9+yPbs2YMeuvnmm0tj6C7/v3t0FxFCx9auXZvt3LkTSNbSpUtL/92ju4gQOqYInT59GkgWEeo9IoSOrVu3rvQ/LZASItR7RAgdI0JIHRHqPSKEjhEhpI4I9R4RQseIEFJHhHqPCKFjn3/+eel/WiAlRKj3iBA6RoSQOiLUe0QIHSNCSB0R6j0ihI4RIaSOCPUeEULHiBBSR4R6jwihY0QIqSNCvUeE0DEihNQRod4jQugYEULqiFDvESF0rJsRGjFiRDZs2LDC66+/XlrmTHzwwQf5evzxuv3444/FY9a8LvWTF/5ynXjuuefO+DFr+cWLF5fGZfr06dnYsWPzaf19b7rppuI6zV933XXF/MmTJ0u3byIi1HtECB3rdoQmT56cbdu2LZs7d26+cdUGwV+uU72KkH8f119/fWmZTtUdoU8//TRbuXJlPu1H6KWXXso2btyYT992223Z8OHDS7cPjZ6rPeZuIUK9R4TQsW5HaMaMGcX8yJEjs1GjRuUb9TfffDO79957s3HjxhXXK1Tjx4/PFi5c2LIe/dzEOeeck913333FBn3mzJnZ5s2bi2U0f/DgwWL+jjvuyMaMGZNf2tipU6eyK664Il/XK6+80nIf7np0H7q0eZuWK6+8MtuwYUO+Dndcex36xdTRo0dnt99+ezE+WIRsz+aFF14oxrT8ggULskmTJmWzZs3KH7fGFR/d52OPPZbPuxF65JFH8utWrVqVz2ud9jz093Mfq2j+0KFDpcej8e+++y4799xz8x/f09jq1auziRMnZpdddll25MiRfEy31bK//vWv8/t68MEHW9ZT9Vpq+U2bNuWvv9an56jHqH+o+I+vTkSo94gQOtbLCGmDo424HabTxvqSSy4prpswYUK2fPnyfPrCCy/Mx3UIT/N33XVXfmkbdF3+/ve/b1m3Np42rdi98cYbeeQUJ23I7f61UdL05ZdfXnrM2pjqOtuo2rx7P3r8r732Wj6tjanGlyxZkk//5je/ycftkNlAETp+/Hh29dVX52HUMtqTsfuQV199NQ+33V4x0fOaNm1aPu9GSFHXco8++mg+P3Xq1HxPSHHbu3dvy2PQD721e0x234sWLcr279+f3X///cX8nDlz8unDhw8X67z22muzp59+Op/WY7N1VL2Wtu4nn3wyX9e7776bz8+fP78lwnUjQr1HhNCxbkdIG0L9i9g2QIqBxm0jLbZBt/kdO3YU87YB1LR7OE6XVRFS2Nx1Ge1ViM0PtiFuN+9OKxL+svop6RdffLEYHyhC5uuvv85jqb+T3Yd7OE7zFkw9h6oI2XIWIf9wnKbt/SLt5Zx33nmlx2Hr8OfdPSbtXc6ePbsUtgMHDuTzg72W2kPy18/huPQQIXSs2xHSv+TnzZuXb7DdcXfjaf/Cdm/rbrhsrJMIaSPpxsa9voq/nH+f/rw7/cknnxTzH374YT6t56tDcjY+UIROnDiRX6f4KBL6u9h9uBFSdCwoZxshvZdkj0OX2svxH49d58/7tPfqR8iWHey11N6Rfx0RSg8RQse6HSH3cJw77m48n3/++ZYNl/2rWtO63L17dz7tR8gOX9m8ImTv6fj3qUNFF1xwQWm8in97d96ddiOkS/v0nw7/2fhAEVIwdUhO07fcckvbCClsdtjSj9CNN95YLKfbtYuQXf/++++3fTy2jD9/9OjR0nJ+hPQ8ND/Ya1kVoW7/xDwR6j0ihI6FECGxDa/e4Ne09iY0bu+J6GPTU6ZMKTZodqhPh4rsPRVFSBtMTduHAx544IF8GX1CT+N670GHBLV35j8u97G0m3en/Qjdeuut+bSCZ+Nr1qzJp3Xp34/eQ9qyZUv++LSMuy4dMtP0s88+m89bCNwIaVrXKQh2O4uQAq15rd/u+5e//GU+5h8Sc/nPXYcJ7T7EXheL0NatW/NDkJq26Gm66rXUdFWE9P7RsWPHWj5YUici1HtECB3rZoTOlOKgT57547J+/frSmA5n6dNW/rhoA1h1G326S0Hyx+ugUNr7J9qouu+ltDvkpMdon37T+ye6/Oijj4rbDPZdH11vt/Npvf6PFvqR6ZT+znpONm8R0h5Q1XMb6LX07dq166y/h9UJItR7RAgdCylC6B7t+Skal156aem6s+EfjgsZEeo9IoSOEaFm0HdxPv7449L42frpp5/y97T88RARod4jQugYEULqiFDvESF0jAghdUSo94gQOkaEkDoi1HtECB3r9nc0gH4jQr1HhNAxIoTUEaHeI0LoGBFC6ohQ7xEhdIwIIXVEqPeIEDpGhJA6ItR7RAgdI0JIHRHqPSKEjhEhpI4I9R4RQscUoZdffhlImv/fPbqLCAEA+oYIAQD6hggBAPqGCAEA+oYIAQD6hggBAPqGCAEA+oYIAQD6hggBAPqGCAEA+oYIAQD6hggBAPqGCAEA+oYIAQD6hggBAPqGCAEA+oYIAQD6hggBAPqGCAEA+oYIAQD6hggBAPrm/wHfYrB3HjuhcwAAAABJRU5ErkJggg==>