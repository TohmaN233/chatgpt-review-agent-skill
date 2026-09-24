# Remote GitHub Workflows

Remote routes use the host’s authorized GitHub connector. Credentials are not
passed through the local MCP bridge.

## `github.review`

1. Pin `owner/repo` and an exact commit SHA, PR head SHA, or immutable tag.
2. Read the required files, diff, issue/PR context, and CI evidence.
3. Give ChatGPT a bounded packet or an approved remote Connector view.
4. Save the result as an artifact with the pinned identity.

Never silently review a moving branch when the request names a release or exact
commit.

## `github.implement`

1. Resolve and record the base commit SHA.
2. Create a new task branch from that SHA.
3. Read each existing file and retain its blob SHA.
4. Apply only authorized writes; fail when a blob or base SHA changed.
5. Commit to the task branch.
6. Create or update a task-owned PR when requested.

Hard boundaries:

- no direct default-branch write;
- no force-push;
- no automatic merge;
- no unpinned base;
- no claim that a branch was pushed unless GitHub returned the resulting commit
  and branch state.
