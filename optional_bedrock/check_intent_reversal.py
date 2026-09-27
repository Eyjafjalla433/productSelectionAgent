"""Read-only diagnostic of current rule coverage. Synthetic catalog, no LLM."""
import json
import agentic_workflow
from mvp.server import AgentRuntime
from mvp.demo import DEMO_CATALOG


def main():
    for reply in ('No cotton', 'Any material is fine', "I don't want cotton anymore",
                  "wait I don't want cotton anymore, could you recommend me some other materials?"):
        runtime = AgentRuntime.create(DEMO_CATALOG)
        sid = runtime.new_session()['session_id']
        before = runtime.chat(sid, 'I want a cotton blue shirt')
        after = runtime.chat(sid, reply)
        fields = ('hard', 'soft', 'excluded')
        print(json.dumps({'reply': reply,
                          'before': {k: before['receipt'].get(k) for k in fields},
                          'after': {k: after['receipt'].get(k) for k in fields}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
