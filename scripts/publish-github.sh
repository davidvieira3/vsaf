#!/usr/bin/env bash
# Publie le dépôt sur GitHub (ou GitHub Enterprise) en privé.
# Usage : ./scripts/publish-github.sh <organisation_ou_utilisateur>/<nom_du_depot>
# GitHub Enterprise : export GH_HOST=github.monentreprise.intra avant de lancer.
set -euo pipefail
REPO="${1:?Usage : $0 organisation/nom-du-depot}"

command -v gh >/dev/null || { echo "Installez GitHub CLI : https://cli.github.com (dnf install gh)"; exit 1; }
gh auth status >/dev/null 2>&1 || gh auth login

if [ -f .env ]; then
  git check-ignore -q .env || { echo "ERREUR : .env n'est pas ignoré par git, publication annulée."; exit 1; }
fi

gh repo create "$REPO" --private --source=. --remote=origin --push \
  --description "Inventaire VMware vCenter / Proxmox VE / baies de stockage, façon RVTools"
echo "Publié : $(gh repo view "$REPO" --json url -q .url)"
