#!/usr/bin/env bash
set -euo pipefail

REPO_NAME="fmr-tracker-app"
GITHUB_USER="kgauvin603"
BRANCH="main"

echo "Checking git repository..."
git rev-parse --is-inside-work-tree >/dev/null 2>&1

echo "Setting branch to ${BRANCH}..."
git branch -M "${BRANCH}"

echo "Adding all changes..."
git add .

if git diff --cached --quiet; then
  echo "No staged changes to commit."
else
  COMMIT_MSG="${1:-Update FMR tracker app}"
  echo "Committing changes: ${COMMIT_MSG}"
  git commit -m "${COMMIT_MSG}"
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "No origin remote found. Adding origin..."
  git remote add origin "https://github.com/${GITHUB_USER}/${REPO_NAME}.git"
fi

echo "Ensuring GitHub repo visibility is public..."
gh repo edit "${GITHUB_USER}/${REPO_NAME}" --visibility public

echo "Pushing to origin/${BRANCH}..."
git push -u origin "${BRANCH}"

echo "Done."
echo "Repo: https://github.com/${GITHUB_USER}/${REPO_NAME}"
