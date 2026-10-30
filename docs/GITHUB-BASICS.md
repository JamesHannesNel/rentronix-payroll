# GitHub basics for this project

## The words

| Word | Meaning here |
| --- | --- |
| Repository (repo) | The project folder on GitHub, with every change ever made to it recorded. |
| Commit | One saved change, with a message saying what changed. Like a restore point. |
| Push | Send commits from a computer up to GitHub. Claude pushes. |
| Pull | Bring the latest commits from GitHub down to a computer. Your VM pulls. |
| Clone | Copy a repository onto a computer for the first time. |
| Branch | A separate line of work. `main` is what runs; bigger changes can be built on another branch first. |
| Pull request (PR) | A proposal to merge a branch into `main`, which you can read and approve first. |
| Deploy key | An SSH key that lets one server read one repository, and nothing else. |
| Actions | GitHub runs the tests on every push. Green tick = passed, red cross = failed. |

## How work flows

```
Claude (cloud) --push--> GitHub (rentronix-payroll) --pull--> Ubuntu VM (runs the app)
```

1. Claude writes code, runs the tests, commits and pushes.
2. GitHub runs the tests again (the Actions tab).
3. On the VM you run `./scripts/update.sh`, which pulls and restarts the app.

## Things to look at on github.com

- **Code** tab: the files as they are now.
- **Commits** (the clock icon with a number, above the file list): every change, newest first. Click one to see exactly which lines changed.
- **Actions** tab: test results for each push.
- **Pull requests** tab: changes waiting for your approval.
- **Settings → Deploy keys**: which servers can read the repository.

## Rules that keep it safe

- Passwords, API secrets and `.env` never go into the repository. `.gitignore` blocks `.env`.
- The repository stays **private**: it contains how your clients' payroll works.
- If a secret is ever committed by mistake, change that secret. Deleting the file is not enough, because the old commit still contains it.

## Commands you may use on the VM

```bash
cd ~/rentronix-payroll
git status          # anything changed on this machine? (should say "nothing to commit")
git log --oneline -10   # the last 10 changes
./scripts/update.sh     # pull the latest code and restart the app
```
