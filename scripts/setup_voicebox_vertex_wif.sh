#!/usr/bin/env bash
set -euo pipefail
project="${GOOGLE_CLOUD_PROJECT:-project-fdadb4dd-cb77-4270-9cd}"
number=$(gcloud projects describe "$project" --format='value(projectNumber)')
account="voicebox-vertex@$project.iam.gserviceaccount.com"
pool=voicebox-actions
gcloud services enable aiplatform.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com --project="$project" --quiet
if ! gcloud iam service-accounts describe "$account" --project="$project" >/dev/null 2>&1; then
  gcloud iam service-accounts create voicebox-vertex --display-name='VOICEBOX Vertex caller' --project="$project" --quiet
fi
gcloud projects add-iam-policy-binding "$project" --member="serviceAccount:$account" --role=roles/aiplatform.user --condition=None --quiet >/dev/null
if ! gcloud iam workload-identity-pools describe "$pool" --location=global --project="$project" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$pool" --location=global --project="$project" --display-name='VOICEBOX Actions' --quiet
fi
mapping='google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref'
condition="assertion.repository == 'nyu1791-collab/voiceBox-Pipeline' && assertion.ref == 'refs/heads/main'"
if gcloud iam workload-identity-pools providers describe github --workload-identity-pool="$pool" --location=global --project="$project" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc github --workload-identity-pool="$pool" --location=global --project="$project" --issuer-uri='https://token.actions.githubusercontent.com' --attribute-mapping="$mapping" --attribute-condition="$condition" --quiet
else
  gcloud iam workload-identity-pools providers create-oidc github --workload-identity-pool="$pool" --location=global --project="$project" --issuer-uri='https://token.actions.githubusercontent.com' --attribute-mapping="$mapping" --attribute-condition="$condition" --quiet
fi
gcloud iam service-accounts add-iam-policy-binding "$account" --project="$project" --role=roles/iam.workloadIdentityUser --member="principalSet://iam.googleapis.com/projects/$number/locations/global/workloadIdentityPools/$pool/attribute.repository/nyu1791-collab/voiceBox-Pipeline" --quiet >/dev/null
printf 'VOICEBOX_VERTEX_PROJECT=%s\nVOICEBOX_WIF_PROVIDER=projects/%s/locations/global/workloadIdentityPools/%s/providers/github\nVOICEBOX_VERTEX_SERVICE_ACCOUNT=%s\n' "$project" "$number" "$pool" "$account"
