# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# ==============================================================================
# Devoxx Belgium 2026 AI Schedule Builder — Project Workflow Automation
# ==============================================================================
# Variables can be overridden via environment variables or command-line arguments:
#   just project=my-other-project region=us-central1 deploy
# ==============================================================================

# Google Cloud Platform configuration
project     := env_var_or_default("GOOGLE_CLOUD_PROJECT", "genai-java-demos")
region      := env_var_or_default("CLOUD_RUN_REGION", "europe-west1")
service     := env_var_or_default("CLOUD_RUN_SERVICE", "dvxaisched")
memory      := env_var_or_default("CLOUD_RUN_MEMORY", "2Gi")
cpu         := env_var_or_default("CLOUD_RUN_CPU", "2")
secret      := env_var_or_default("CLOUD_RUN_SECRET", "GEMINI_API_KEY=DEVOXX_GEMINI_API_KEY:latest")
max_instances := env_var_or_default("CLOUD_RUN_MAX_INSTANCES", "3")
concurrency := env_var_or_default("CLOUD_RUN_CONCURRENCY", "20")

# Conference data configuration
event_slug  := env_var_or_default("DEVOXX_EVENT_SLUG", "dvbe26")

# Default: List all available recipes
default:
    @just --list

# Resolve Java/Python dependencies and generate editor stubs
install:
    pyronaut install

# Run all unit and integration test suites (no API key needed: uses a fake LLM)
test:
    pyronaut test

# Run the application locally with live reload (port 8080)
dev:
    pyronaut dev

# Run the application locally without live reload
run:
    pyronaut run

# Validate application configuration for production
validate:
    pyronaut validate-config --scenario production

# Fetch the latest official schedule from the CFP API and update resources
fetch-schedule slug=event_slug:
    uv run --no-project --script scripts/fetch_schedule.py {{slug}}

alias fetch := fetch-schedule

# Package the runnable fat JAR (dist/dvxaisched-0.1.0.jar)
build:
    pyronaut build --jar

# Build a JVM container image with Pyronaut's packager
docker:
    pyronaut build --jvm --docker

# Clean generated Pyronaut state and build artifacts
clean:
    pyronaut clean
    rm -rf dist

# Build the fat JAR and deploy it to Google Cloud Run (GraalVM JDK container)
deploy: build
    gcloud run deploy {{service}} \
        --source=. \
        --region={{region}} \
        --project={{project}} \
        --set-secrets={{secret}} \
        --memory={{memory}} \
        --cpu={{cpu}} \
        --max-instances={{max_instances}} \
        --concurrency={{concurrency}} \
        --allow-unauthenticated \
        --quiet

# Show Cloud Run service status, URL, and revision
status:
    gcloud run services describe {{service}} \
        --project={{project}} \
        --region={{region}} \
        --format="table(status.url:label=URL,status.latestReadyRevisionName:label=REVISION,status.conditions[0].status:label=READY)"

# Tail live application logs from Cloud Run
logs:
    gcloud beta run services logs tail {{service}} \
        --project={{project}} \
        --region={{region}}
