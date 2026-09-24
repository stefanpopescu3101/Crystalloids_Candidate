# Posts API

FastAPI application using Google ID tokens and Firestore. All post endpoints require
login. Everyone signed in can read posts; only the author can update or delete them.
Authors use Google's stable `sub` identifier, not an email address.

## Local development

Use Python 3.11 or newer (developed with Python 3.13).

```sh
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

The example configuration enables `ALLOW_GCLOUD_DEV_TOKENS=true` for this interview's
CLI workflow. No new OAuth client or client secret is needed. Log in once with
`gcloud auth login`, then generate an ID token with `gcloud auth print-identity-token`.
Never use `gcloud auth print-access-token` here: it produces a different token type.

The API always verifies Google's signature, expiry, issuance time and issuer, then
uses the verified `sub` claim as the author. CLI development mode skips audience
validation because generic CLI tokens are not scoped to this app. This means it accepts
valid Google ID tokens regardless of intended audience; it does not verify that the
caller belongs to this Cloud project. This mode is for local development and the
controlled interview demo, not public production authentication.

For production app sign-in, set `ALLOW_GCLOUD_DEV_TOKENS=false` and configure
`GOOGLE_OAUTH_CLIENT_ID` as the expected audience. Startup fails if neither mode is
configured, or if both settings conflict. For a private Cloud Run demo, also require
Cloud Run IAM authentication and grant intended testers the Cloud Run Invoker role.
Cloud Run IAM and the application's JWT/ownership checks are separate layers.

For real Firestore, provision a Firestore Native database and set its project/database
in `.env`. Authenticate the Python client using Application Default Credentials:

```sh
gcloud auth application-default login
uvicorn app.main:app --reload --port 8000
```

`gcloud auth login` alone does not configure Application Default Credentials.
The development identity needs Firestore data access (typically `roles/datastore.user`).
On Cloud Run, use the service's attached service account instead of a credential file.

Alternatively, start a Firestore emulator in a separate terminal:

```sh
gcloud emulators firestore start --host-port=127.0.0.1:8080
```

Export `FIRESTORE_EMULATOR_HOST=127.0.0.1:8080` in the API's shell before starting it.
The emulator variable must be an actual environment variable for Google's SDK;
putting it only in `.env` is insufficient. User authentication still requires Google
ID tokens. There is no development authentication bypass.

## Endpoints

| Method | Path | Operation |
| --- | --- | --- |
| POST | `/posts` | Add Post |
| GET | `/posts` | Get all Posts |
| GET | `/posts/{post_id}` | Get a specific Post |
| GET | `/users/{user_id}/posts` | Get all Posts from a user |
| PATCH | `/posts/{post_id}` | Update Post |
| DELETE | `/posts/{post_id}` | Delete Post |
| POST | `/posts/{post_id}/comments` | Add Comment |
| GET | `/posts/{post_id}/comments` | Get comments for a Post |
| PATCH | `/posts/{post_id}/comments/{comment_id}` | Update Comment |
| DELETE | `/posts/{post_id}/comments/{comment_id}` | Delete Comment |

Comments are stored at `posts/{post_id}/comments/{comment_id}`. Any authenticated
user can add or read a comment on an existing post. The API derives the comment author
and timestamps from the authenticated request. Only that comment's author can update
or delete it; this includes the post author, who has no special authority over others'
comments. A post deletion makes its comments inaccessible because their parent no
longer exists; Firestore retains subcollection documents until a separate cleanup job
removes them.

Interactive documentation: http://localhost:8000/docs. Click **Authorize** and paste
the output of `gcloud auth print-identity-token` in development mode. In Postman,
choose **Bearer Token** and paste the same token. Refresh it when it expires.
Keep tokens private and out of Git. Login/token acquisition is handled by the CLI,
not a username/password endpoint in this API.

### Postman

Import [the collection](postman/Posts_API.postman_collection.json) and
[cloud environment](postman/Crystalloids_Cloud.postman_environment.json). In a terminal,
run `gcloud auth print-identity-token`, then paste the output into the environment's
`token` variable. Select `Crystalloids Cloud` and send **Create Post** first; its test
script saves the returned `post_id`. **Create Comment** likewise saves `comment_id`.
The collection supplies both required Cloud Run headers. The environment has no token
value and can safely be committed. For local testing, change `base_url` to
`http://127.0.0.1:8000` and disable every `X-Serverless-Authorization` header.

```sh
export TOKEN="$(gcloud auth print-identity-token)"
curl -X POST http://localhost:8000/posts \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"subject":"Hello","body":"My first post"}'

curl 'http://localhost:8000/posts?limit=20' -H "Authorization: Bearer $TOKEN"

curl -X POST 'http://localhost:8000/posts/POST_ID/comments' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"body":"A comment from this user"}'
```

Create/update input accepts only `subject` (1–200 characters) and `body` (1–20,000
characters); both are trimmed. Creation requires both. PATCH requires at least one
and rejects explicit nulls. The API generates UTC timestamps and the author.

Lists return `{"items": [...], "next_cursor": "..."}`. Pass `next_cursor` as the next
request's `cursor`, keeping the same user filter. A null cursor means no further page.
Default page size is 20, maximum 100. Ordering is ascending document ID, **not time**.
Pagination is not a snapshot: concurrent creations can appear before an existing cursor.
Firestore's default single-field indexes support the author filter with document-ID
ordering; no custom chronological index is needed.

Responses: create 201, read/update 200, delete 204, invalid/missing token 401,
non-author mutation 403, missing post 404, invalid input 422, unavailable storage or
token-verification transport 503. `/health` and API documentation are public and expose
no post data. Health is a liveness check, not a Firestore readiness check.

## Verification

```sh
pytest
ruff check .
```

API tests replace storage with a test double and mock Google's token verifier. Separate
authentication tests use signed test JWTs and the real verifier, replacing only the
certificate download; they check invalid signatures, expiry, issuer and audience modes.
These tests do not prove live Google authentication or Firestore connectivity.
Update/delete ownership checks and
writes run in Firestore transactions to handle concurrent changes safely.

## Cloud Build deployment

`Dockerfile` provides a test stage and a non-root runtime stage that listens on Cloud
Run's `PORT`. `cloudbuild.yaml` runs lint and tests, builds the runtime image, pushes
it to Artifact Registry, and deploys an authenticated Cloud Run service. Each image
is tagged with its unique Cloud Build ID. Upload and Docker allowlists exclude local
credentials, `.env`, and `.venv`.

The configured resources are:

| Resource | Value |
| --- | --- |
| Project | `crystalloids-candidates` |
| Cloud Run service / region | `stefan-popescu-posts` / `europe-west4` |
| Artifact Registry repository / region | `stefantest-repo` / `europe-west1` |
| Firestore database | `stefan-database` |
| Runtime identity | `stefanservice@crystalloids-candidates.iam.gserviceaccount.com` |

Submit the repository's working directory with:

```sh
gcloud builds submit . --config=cloudbuild.yaml \
  --project=crystalloids-candidates --region=europe-west4
```

The same configuration can be selected by a Cloud Build Git repository trigger.
Submitting locally uploads the current source; it does not create a Git trigger or
push commits to GitHub. Change resource settings through the substitutions at the
bottom of `cloudbuild.yaml` or `gcloud builds submit --substitutions=...`.

The build identity needs permission to push to the repository, deploy Cloud Run,
and act as the runtime service account. The runtime service account needs
`roles/datastore.user` on the intended database (or an appropriate project grant).
These permissions must be configured separately from the build pipeline.

A project IAM administrator can grant the runtime identity access to only this
database with:

```sh
gcloud projects add-iam-policy-binding crystalloids-candidates \
  --member=serviceAccount:stefanservice@crystalloids-candidates.iam.gserviceaccount.com \
  --role=roles/datastore.user \
  --condition='expression=resource.name=="projects/crystalloids-candidates/databases/stefan-database",title=stefan-database-access'
```

This uses Google's documented [per-database IAM condition](https://cloud.google.com/firestore/docs/manage-databases).
The workstation account currently cannot change project IAM policies. If the API
returns `503 Post storage unavailable` and logs show `PermissionDenied`, confirm this
grant before changing application code.

The service requires Cloud Run Invoker permission as well as the application's
Google JWT authentication. `_CLI_TOKEN_AUDIENCE` configures the custom audience
used by this workstation's CLI login; it is a public identifier, not a secret.
This lets Cloud Run accept those tokens while still checking IAM invocation rights.
After deployment:

```sh
SERVICE_URL="$(gcloud run services describe stefan-popescu-posts \
  --project=crystalloids-candidates --region=europe-west4 \
  --format='value(status.url)')"
TOKEN="$(gcloud auth print-identity-token)"
curl "$SERVICE_URL/posts" \
  -H "X-Serverless-Authorization: Bearer $TOKEN" \
  -H "Authorization: Bearer $TOKEN"
```

The first header authenticates to Cloud Run; the second preserves the signed token
for application verification. The deployment explicitly enables CLI development
tokens for the controlled demo. Use app-specific audience validation for production.

## Comment analytics: Pub/Sub to BigQuery

Each successful `POST /posts/{post_id}/comments` publishes a JSON `comment.created`
event to `stefan-comments-topic`. The event includes `event_id` (the comment ID), post
and comment IDs, the verified Google-account email address, comment body, and creation
time. The endpoint waits for Pub/Sub acknowledgement. If Firestore accepts a comment
but Pub/Sub is unavailable, the request returns `503`; a production system should add
an outbox/retry worker to close that delivery gap.

The provisioned analytics destination is
`crystalloids-candidates.stefan_comments_analytics.comment_events` in `europe-west4`.
The pending subscription is named `stefan-comments-bigquery` and uses the BigQuery
table schema, mapping JSON message fields directly to BigQuery columns. Pub/Sub
delivery is at-least-once, so analytics queries should deduplicate on `event_id`.

```sql
SELECT event_id, author_email, body, created_at
FROM `crystalloids-candidates.stefan_comments_analytics.comment_events`
QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY created_at DESC) = 1
ORDER BY created_at DESC;
```

An administrator must run these one-time commands before deployment can publish and
BigQuery can receive events:

```sh
gcloud projects add-iam-policy-binding crystalloids-candidates \
  --member=serviceAccount:stefanservice@crystalloids-candidates.iam.gserviceaccount.com \
  --role=roles/pubsub.publisher

gcloud pubsub subscriptions create stefan-comments-bigquery \
  --project=crystalloids-candidates \
  --topic=stefan-comments-topic \
  --bigquery-table=crystalloids-candidates:stefan_comments_analytics.comment_events \
  --use-table-schema
```

The project Pub/Sub service agent needs BigQuery Data Editor access to write the
table. The subscription creation itself requires `pubsub.subscriptions.create`, which
this workstation identity does not have.

## Next milestones

Configure production Google sign-in with an app-specific audience and add an outbox
worker for reliable Firestore-to-Pub/Sub delivery.

References: [Google ID token verification](https://developers.google.com/identity/sign-in/web/backend-auth)
and [Cloud Run developer authentication](https://docs.cloud.google.com/run/docs/authenticating/developers)
and [Firestore query cursors](https://docs.cloud.google.com/python/docs/reference/firestore/latest/google.cloud.firestore_v1.query.Query).
