# Setting up your dynamic profile card

This mirrors Andrew Grant's (Andrew6rant) approach: a GitHub Action runs a Python
script daily that queries the GitHub GraphQL API for your live stats (repos, stars,
commits, followers, lines of code) and writes them directly into `dark_mode.svg` /
`light_mode.svg`. `README.md` just embeds those two images.

## 1. Push these files to your `Raph1710/Raph1710` repo

If you don't already have a repo named exactly `Raph1710` (matching your username),
create one — GitHub treats a repo named after your username specially and shows its
README on your profile page.

```
git clone https://github.com/Raph1710/Raph1710.git
# copy all files from this bundle into that folder, then:
cd Raph1710
git add .
git commit -m "Dynamic profile card"
git push
```

## 2. Create a fine-grained Personal Access Token

Go to **GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.

- **Repository access**: All repositories (needed so it can read commit history across your repos for the lines-of-code count)
- **Account permissions**: `Followers: Read-only`
- **Repository permissions**: `Commit statuses: Read-only`, `Contents: Read-only`, `Metadata: Read-only`

Copy the token — you won't see it again.

## 3. Add repo secrets

In the `Raph1710/Raph1710` repo: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `ACCESS_TOKEN` | the token you just generated |
| `USER_NAME` | `Raph1710` |

## 4. Enable Actions and run it

Go to the **Actions** tab, enable workflows if prompted, then run **README build**
manually once (or just push — it also triggers on push to `main`). It also runs
automatically every day at 04:00 UTC via the cron schedule, so your stats stay fresh.

## 5. Edit the placeholders

A few fields are placeholders you should personalize before (or after) your first push —
open `today.py` and `*.svg` and adjust:

- **`CODING_START_DATE`** in `today.py` — currently set to `2022-01-01`. Change it to
  when you started coding seriously; it drives the "Coding Since" line.
- **IDE, Focus, Education** text in the SVGs — edit the `<tspan class="value">...</tspan>`
  text directly if you want to tweak the wording.
- **Projects section** — currently lists AI Reading Assistant and WatchWise; edit those
  two rows in the SVGs (or add more, following the same `<tspan>` pattern) as you ship
  new things.

Everything with an `id="..._data"` attribute (repos, stars, commits, followers, lines
of code, coding-since) is overwritten automatically by `today.py` on every run — don't
hand-edit those, your edits will be replaced on the next Action run.

## Notes

- First run will be slower (no cache yet) since it walks every repo's commit history to
  count lines of code. Subsequent runs only re-scan repos where the commit count changed.
- If a run fails with a 403, GitHub's abuse-detection rate limit was hit — it'll succeed
  on the next scheduled run.
