# Preview implementation ledger

Ruling: The user's selection approves filling the selected design, as explicitly requested in the prior turn. Continue within that scope without another design approval round.
Ruling: New standalone frontend folder is isolated from the original backend checkout; no worktree needed.
Ruling: Use the actual existing demo HTTP runtime rather than duplicate its selection and conversational behavior in a mock frontend. Costs: two local services; benefits: truthful API field validation.
Plan preflight: model contracts are the only shared boundary; components and state hook consume those exact types. No conflicting interfaces found.
Task 1: in progress.
