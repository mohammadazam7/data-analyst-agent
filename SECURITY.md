# Security policy

## Reporting a problem

Please report security issues privately through GitHub's
[security advisories](https://github.com/mohammadazam7/data-analyst-agent/security/advisories/new)
rather than in a public issue. Expect an acknowledgement within a few days.

## How the agent is constrained

An agent that writes and runs SQL is an attack surface, so the design assumes the model
can be wrong or manipulated:

- The agent connects with read-only credentials. Every statement is parsed before it
  runs, and anything other than a single `SELECT` (or `WITH ... SELECT`) is refused.
- Queries run with a row limit, a byte or cost budget where the warehouse supports one,
  and a timeout.
- Table and column names, values from the data and user questions are treated as data,
  never as instructions to the agent.
- API keys for model providers and warehouse credentials are read from the environment
  and are never committed. `.gitignore` excludes common key file names.

Reports of a way to make the agent run a write, read outside its allowed schemas or leak
credentials are especially welcome.
