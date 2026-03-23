"""
Eval framework for testing coding agent CLIs.

Runs structured tasks through the CodingAgent with different CLI backends
and grades results by running the task's test suite.

Architecture:
    EvalTask (task.yaml + seed repo with failing tests)
        → EvalRunner (spins up GitWorkspace, invokes CodingAgent)
            → EvalResult (pass/fail, timing, files changed)
                → EvalReport (aggregate across tasks × CLIs)
"""
