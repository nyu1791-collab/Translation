#!/usr/bin/env bash
# Run in Cloud Shell under the Google Cloud project owner account.
set -euo pipefail
translation_project="${GOOGLE_CLOUD_PROJECT:-project-fdadb4dd-cb77-4270-9cd}"
translation_repo="${TRANSLATION_GITHUB_REPO:-nyu1791-collab/Translation}"
translation_number=$(gcloud projects describe "$translation_project" --format='value(projectNumber)')
translation_sa="translation-vertex@$translation_project.iam.gserviceaccount.com"
translation_pool='translation-actions'
translation_provider='github'
translation_enabled=$(gcloud services list --enabled --project="$translation_project" --format='value(config.name)')
translation_missing=()
for translation_service in aiplatform.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com; do
  if ! grep -Fxq "$translation_service" <<<"$translation_enabled"; then
    translation_missing+=("$translation_service")
  fi
done
if ((${#translation_missing[@]})); then
  translation_error=$(mktemp)
  if gcloud services enable "${translation_missing[@]}" --project="$translation_project" --quiet 2>"$translation_error"; then
    rm -f "$translation_error"
  elif grep -qi 'RATE_LIMIT_EXCEEDED' "$translation_error"; then
    echo 'Google Cloud service activation is rate limited; waiting 60 seconds before one retry.' >&2
    rm -f "$translation_error"
    sleep 60
    gcloud services enable "${translation_missing[@]}" --project="$translation_project" --quiet
  else
    cat "$translation_error" >&2
    rm -f "$translation_error"
    exit 1
  fi
else
  echo 'Required Google Cloud services are already enabled.'
fi
if ! gcloud iam service-accounts describe "$translation_sa" --project="$translation_project" >/dev/null 2>&1; then
  gcloud iam service-accounts create translation-vertex --display-name='Translation Vertex caller' --project="$translation_project" --quiet
fi
gcloud projects add-iam-policy-binding "$translation_project" --member="serviceAccount:$translation_sa" --role="roles/editor" --condition=None --quiet >/dev/null
if ! gcloud iam workload-identity-pools describe "$translation_pool" --location=global --project="$translation_project" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$translation_pool" --location=global --display-name='Translation Actions' --project="$translation_project" --quiet
fi
translation_mapping='google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref'
translation_condition="assertion.repository == '$translation_repo' && assertion.ref == 'refs/heads/main'"
if gcloud iam workload-identity-pools providers describe "$translation_provider" --workload-identity-pool="$translation_pool" --location=global --project="$translation_project" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc "$translation_provider" --workload-identity-pool="$translation_pool" --location=global --project="$translation_project" --issuer-uri='https://token.actions.githubusercontent.com' --attribute-mapping="$translation_mapping" --attribute-condition="$translation_condition" --quiet
else
  gcloud iam workload-identity-pools providers create-oidc "$translation_provider" --workload-identity-pool="$translation_pool" --location=global --project="$translation_project" --issuer-uri='https://token.actions.githubusercontent.com' --attribute-mapping="$translation_mapping" --attribute-condition="$translation_condition" --quiet
fi
gcloud iam service-accounts add-iam-policy-binding "$translation_sa" --project="$translation_project" --role=roles/iam.workloadIdentityUser --member="principalSet://iam.googleapis.com/projects/$translation_number/locations/global/workloadIdentityPools/$translation_pool/attribute.repository/$translation_repo" --quiet >/dev/null
translation_wif="projects/$translation_number/locations/global/workloadIdentityPools/$translation_pool/providers/$translation_provider"
echo "TRANSLATION_PROJECT_ID=$translation_project"
echo "TRANSLATION_SERVICE_ACCOUNT=$translation_sa"
echo "TRANSLATION_WIF_PROVIDER=$translation_wif"
translation_entry=$(TRANSLATION_MAP_PROJECT="$translation_project" TRANSLATION_MAP_SA="$translation_sa" TRANSLATION_MAP_WIF="$translation_wif" python3 - <<'PYCODE'
import json, os
print(json.dumps({os.environ["TRANSLATION_MAP_PROJECT"]: {"provider": os.environ["TRANSLATION_MAP_WIF"], "service_account": os.environ["TRANSLATION_MAP_SA"]}}, separators=(",", ":")))
PYCODE
)
echo "Add this entry to TRANSLATION_PROJECTS if automatic registration is unavailable: $translation_entry"
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  translation_map_file=$(mktemp)
  translation_gh_error=$(mktemp)
  translation_map_ready=false
  translation_map_registered=false
  if gh variable get TRANSLATION_PROJECTS --repo "$translation_repo" >"$translation_map_file" 2>"$translation_gh_error"; then
    translation_map_ready=true
  elif grep -Eqi 'HTTP 404|not found' "$translation_gh_error"; then
    printf '{}\n' >"$translation_map_file"
    translation_map_ready=true
  else
    echo 'Cannot read existing TRANSLATION_PROJECTS; leaving GitHub variables unchanged.' >&2
  fi
  if [ "$translation_map_ready" = true ]; then
    if translation_merged=$(TRANSLATION_MAP_ENTRY="$translation_entry" python3 - "$translation_map_file" <<'PYCODE'
import json, os, sys
existing = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(existing, dict):
    raise ValueError("TRANSLATION_PROJECTS must be a JSON object")
existing.update(json.loads(os.environ["TRANSLATION_MAP_ENTRY"]))
print(json.dumps(existing, ensure_ascii=False, separators=(",", ":")))
PYCODE
    ); then
      if gh variable set TRANSLATION_PROJECTS --repo "$translation_repo" --body "$translation_merged"; then
        translation_map_registered=true
        echo 'Project added to GitHub Actions TRANSLATION_PROJECTS.'
      else
        echo 'Could not register project; add the entry printed above manually.' >&2
      fi
    else
      echo 'Invalid existing TRANSLATION_PROJECTS; it was not overwritten.' >&2
    fi
  fi
  rm -f "$translation_map_file" "$translation_gh_error"
  if [ "${TRANSLATION_SET_DEFAULT:-0}" = 1 ]; then
    if [ "$translation_map_registered" != true ]; then
      echo 'Default project was not changed because the new project was not registered.' >&2
    elif gh variable set TRANSLATION_PROJECT_ID --repo "$translation_repo" --body "$translation_project"; then
      echo 'Default project updated.'
    else
      echo 'Could not update default project; set TRANSLATION_PROJECT_ID manually.' >&2
    fi
  fi
else
  echo 'Set the TRANSLATION_PROJECTS entry printed above in GitHub Actions variables.'
fi
echo 'Set TRANSLATION_STORAGE_TOKEN as an Actions secret with access to the private results repository.'
