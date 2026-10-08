# Benchmark Artifacts

All model runs use the same ten questions, execution-based row comparison, read-only SQLite path, low reasoning effort, and telemetry-enabled evaluator. These files preserve experiments; `../dev_answers.json` remains the required canonical submission artifact.

| Model | Accuracy | P50 | Directory |
|---|---:|---:|---|
| Kimi K2.6, cache-affinity run | 10/10 | 7.271s | [`kimi_k2p6/`](kimi_k2p6/) |
| Kimi K3 | 9/10 | 5.217s | [`kimi_k3/`](kimi_k3/) |
| DeepSeek V4 Pro (0813) | 10/10 | 6.089s | [`deepseek_v4_pro_0813/`](deepseek_v4_pro_0813/) |
| GPT-OSS 120B | 8/10 | 1.022s | [`gpt_oss_120b/`](gpt_oss_120b/) |
| GLM 5.3 Fast, Run A | 10/10 | 0.804s | [`glm_5p3_fast/`](glm_5p3_fast/) |
| GLM 5.3 Fast, three-run aggregate | 28/30 | 0.716s | [`glm_5p3_fast/`](glm_5p3_fast/) |

Each model directory contains generated answers, detailed JSON telemetry, and its standalone HTML analysis where available. See [`../README.md`](../README.md) for methodology, caveats, and the production recommendation.
