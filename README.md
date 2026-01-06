
## 1. Branching Strategy

Slash-based naming convention

**Example:** `type/description-separated-by-dashes`

### Allowed Branch Types

| Type | Purpose |
|------|---------|
| `feat/` | New features |
| `fix/` | Bug fixes |
| `docs/` | Documentation updates |
| `refactor/` | Code restructuring without behavioral changes |
| `test/` | Adding or modifying tests |

---

## 2. Commit Message Convention

**[Conventional Commits](https://www.conventionalcommits.org/)**

### Language Style

Imperative mood == If applied this commit will...

### Structure

```
<type>(<scope>): <description>

<body>

<footer>
```

### Explanations

Both body and footer are optional and can occur multiple time on multiple rows.

- **Description**: Keep it short and concise—ideally under 50 characters. This is a brief summary of *what* changed.
- **Body**: Use this for *why* and *how* if the change needs more explanation. Separate from the description with a blank line.
- **Footer**: Used for referencing or noting breaking changes.

### Commit Types

| Type | Description |
|------|-------------|
| `feat` | introduces a new feature |
| `fix` | patches a bug |
| `docs` | documentation only changes |
| `style` | changes that do not affect code meaning (formatting, etc.) |
| `refactor` | code change that neither fixes a bug nor adds a feature |
| `perf` | code change that improves performance |
| `test` | adding or correcting tests |
| `build` | changes to build system or external dependencies |
| `ci` | changes to ci configuration files and scripts |
| `chore` | other changes that don't modify src or test files |
| `revert` | reverts a previous commit |

### Footer Types

| Type | Description |
|------|-------------|
| `closes #` | Automatically closes the linked issue when the commit is merged. |
| `fixes #` | Specifically indicates a bug fix and automatically closes the issue. |
| `docs #` | References a documentation-specific issue (used for linking/tracking). |
| `resolves #` | Formally indicates that the work for an issue is completed; closes the issue. |
| `refs #` | Links to an issue or pull request for context without closing it. |
| `references #` | Same as `refs`. |
| `relates to #` | Indicates a non-direct relationship with an issue or topic. |
| `reverts #` | References an issue or pull request that is being undone by this commit. |
| `see #` | Points to an issue or discussion for additional reading or context. |
| `Co-authored-by: ` | Gives credit to another person who worked on the code (Format: `Name <email>`). |
| `Signed-off-by: ` | A digital signature certifying that the contributor has the right to submit the code. |
| `Reviewed-by: ` | Documents who performed the internal code review for this change. |
| `Reported-by: ` | Acknowledges the person who found or reported the bug being fixed. |
| `CC: ` | Notifies specific individuals about the commit (Carbon Copy). |
| `See also: ` | Provides links to external documentation, URLs, or related resources. |
| `BREAKING CHANGE: ` | Signals a change that breaks backward compatibility (triggers a Major version bump). |

> **Note:** Referring to individuals with the format: `[Name] <[email]>`.

> **Note:** If a `BREAKING CHANGE` description spans multiple lines, subsequent lines must be indented (with spaces) to indicate they belong to the same footer token.

### 2.1 Handling Breaking Changes

A breaking change indicates that the code change renders previous versions incompatible. You can use either or both of these methods:

**Option A: Exclamation mark (`!`)**

Append an exclamation mark after the type (and scope, if present):

```
feat!: remove support for python 3.8
chore(db)!: drop user_id column
```

**Option B: Footer**

Include a footer starting with `BREAKING CHANGE:`:

```
feat: allow provided config object to extend other configs

BREAKING CHANGE: `extends` key in config file is now used for extending other config files
```

**Option C: Both (for extra visibility)**

```
feat!: allow provided config object to extend other configs

BREAKING CHANGE: `extends` key in config file is now used for extending other config files
```

## 3. Development Environment Setup

### Activate venv

**Windows:**
```bash
.venv\Scripts\activate
```

**macOS / Linux:**
```bash
source .venv/bin/activate
```

### De-activate venv

```bash
deactivate
```

### Install Pre-commit Hooks

(with venv if used)

```bash
pip install pre-commit
pre-commit install --hook-type commit-msg
```
