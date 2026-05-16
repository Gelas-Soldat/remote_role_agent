# Run this from the root of your local remote_role_agent repo after copying this patch folder nearby.
# Example:
#   cd C:\Dev\remote_role_agent
#   Copy-Item -Recurse C:\Downloads\remote_role_agent_repo_polish_patch\* . -Force
#   git status

git status
Write-Host "Review the changed files above. If correct, run:" -ForegroundColor Cyan
Write-Host "git add README.md CONTRIBUTING.md SUPPORT.md docs/REPO_POLISH_CHECKLIST.md .github/FUNDING.yml .github/PULL_REQUEST_TEMPLATE.md .github/ISSUE_TEMPLATE" -ForegroundColor Yellow
Write-Host "git commit -m 'Add repository support and contribution files'" -ForegroundColor Yellow
Write-Host "git push" -ForegroundColor Yellow
