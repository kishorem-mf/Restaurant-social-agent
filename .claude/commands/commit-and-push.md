---
command: "/commit-and-push"
description: "Stage all changes, commit with message, and push to origin"
arguments:
  - name: "message"
    description: "Commit message"
    required: true
---

# Commit and Push

Execute the following git workflow:

1. **Stage all changes**
   ```bash
   git add .
   ```

2. **Commit with provided message**
   ```bash
   git commit -m "$ARGUMENTS"
   ```

3. **Push to origin**
   ```bash
   git push origin
   ```

## Usage

```
/commit-and-push "your commit message here"
```

## Example

```
/commit-and-push "feat: add Instagram scraper functionality"
```
