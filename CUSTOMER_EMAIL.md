Subject: Fireworks text-to-SQL CLI proof of concept

Hi Raul,

I built a Fireworks text-to-SQL CLI PoC against the supplied Czech banking
database. Users ask natural-language questions, see the generated SQL and results,
and can ask follow-ups. Compared with the baseline prompt, I added live schema and
column-documentation grounding, structured output, read-only and single-statement
checks, one error-driven repair, bounded chat memory, and prompt-cache affinity.

**Key findings:** GLM 5.3 Fast is the strongest latency candidate. Across three
complete development-set runs, it scored **28/30 (93.3%)**, with combined P50
**0.72s**, P95 **5.81s**, and **27/30** requests below the 3-second target. Prompt
cache hit rate was **89.3%** overall and **99.5%** in the latest run. The first run
scored 10/10, but Runs 2 and 3 repeated the same semantic error: treating Prague as
a district name instead of a region. Because that SQL executed successfully, the
existing error-repair path could not detect it.

**Model tradeoff:** Kimi K2.6 and DeepSeek V4 Pro each scored **10/10**, but their
P50 latency was **7.27s** and **6.09s**, respectively. GPT-OSS 120B reached **1.02s**
P50 but scored **8/10**. GLM 5.3 Fast is therefore the best route toward the latency
goal, but it needs semantic validation and a quality fallback before production.

**Cost:** GLM 5.3 Fast serverless list pricing is **$2.10 / $0.39 / $6.60** per 1M
tokens (uncached input / cached input / output). The three observed runs cost an
estimated **$0.0313 total**, or about **$0.00104/query**. At 30,000 queries/day,
that projects to roughly **$31/day** or **$940/month** for model inference at this
prompt size and cache behavior, excluding application and database infrastructure.

**Validation:** I ran 30 independent benchmark cases across three complete runs,
executed every generated query against SQLite, and compared ordered result values
with the expected rows. The evaluator records end-to-end latency, API calls, prompt,
cached, completion and reasoning tokens, Fireworks TTFT and server processing, SQL
repair use, and SQLite execution. A separate 30-replay-per-query check measured
**6.53ms median SQLite execution**; model and routing behavior, not ordinary database
execution, controls interactive latency. See the artifacts under
`results/glm_5p3_fast/`.

**Next (1-2 week spike):** (1) add schema-driven semantic checks for geographic
columns, date formats, and exact categorical values; (2) route validation failures
to a current higher-quality fallback model; (3) evaluate unseen paraphrases and
customer schemas in CI; (4) repeat cold, warm, and concurrent load tests; and (5)
add production guardrails including least-privilege credentials, timeouts, row
limits, and query audit logs.

Happy to walk through the code and scorecard live. I used AI assistance for
scaffolding and prompt iteration, then reviewed the implementation and validated
all reported results against executable gold answers.

Best,

Dian Yu