# gha-reusable-terraform

GitHub Actions reusable workflows for Terraform Plan/Apply/Destroy, Lambda builds, ECR Docker builds, and website deployments.

## Workflows

| Workflow | Description |
| --- | --- |
| [terraform-run.yml](#terraform-runyml) | Runs Terraform plan/apply/destroy against a shared S3 backend and posts a report to the job summary and PR |
| [lambda-build.yml](#lambda-buildyml) | Detects Lambda functions and builds/packages them (with optional layers), then uploads the artifacts to S3 |
| [ecr-docker-build.yml](#ecr-docker-buildyml) | Detects Dockerfiles in subdirectories, builds images, and optionally pushes them to ECR |
| [deploy-website.yml](#deploy-websiteyml) | Downloads Terraform outputs, performs SSM parameter replacements, syncs files to an S3 bucket, and invalidates CloudFront |
| [delete-labels.yml](#delete-labelsyml) | Deletes all labels in a repository |
| [new-repo.yml](#new-repoyml) | Manual setup workflow that prepares a repository for its first IaC sync |

## Usage

Call any reusable workflow from the caller repository:

```yaml
jobs:
  terraform:
    uses: faccomichele/gha-reusables/.github/workflows/terraform-run.yml@main
    with:
      environment: dev
      region: eu-west-1
    secrets: inherit
```

## terraform-run.yml

Runs Terraform against a shared state file stored in S3 and generates a Markdown report with the plan/apply output, added to the job summary and (on pull requests) as a PR comment.

### Inputs

| Input | Type | Default | Required | Description |
| --- | --- | --- | --- | --- |
| `environment` | string | - | Yes | The target environment (dev, stg, prod), required when s3-bucket is provided |
| `environment-prefix` | string | `''` | No | Optional prefix for the environment name (e.g., `myorg-`). When set, it is prepended to the environment name and used in the Terraform workspace and Project tag. |
| `region` | string | - | Yes | AWS region for the S3 bucket |
| `global` | boolean | `false` | No | When true, Terraform treats the run as global (no region in workspace/varfile/terraform env), while AWS CLI still uses the provided region |
| `validation-environment` | string | `''` | No | Optional validation environment suffix (e.g., `val1`). When set, it is appended to the Terraform workspace and Project tag |
| `action` | string | `plan` | No | The Terraform action to perform: `plan` (review changes only), `apply` (apply changes), `destroy` (destroy resources), or `state` (run `state rm`) |
| `action-arguments` | string | `''` | No | Optional additional arguments to pass to the Terraform action (e.g., `-target=module.example`) |
| `working-directory` | string | `.` | No | The location containing the terraform related files |
| `lambda-working-directory` | string | `./lambdas` | No | The location containing the lambda related files to be uploaded to S3 for use in Terraform |
| `upload-output-artifact` | boolean | `false` | No | If true, upload the terraform output artifact from the current workflow run, otherwise it skips it |
| `skip-terraform-report` | boolean | `false` | No | If true, skip generating the Terraform report and adding it to the job summary and PR comment |

### Secrets

- `ACCOUNT_ID`, `ROLE_SECRET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` - AWS authentication (passed to the custom AWS login composite)
- `GH_APP_CLIENT_ID`, `GH_APP_PRIVATE_KEY` - optional GitHub App credentials for a temporary token (used for `TF_VAR_github_token`)
- `TF_VAR_DIGITALOCEAN_TOKEN` - optional DigitalOcean token exposed to Terraform as `TF_VAR_digitalocean_token`

### Example

```yaml
jobs:
  terraform:
    uses: faccomichele/gha-reusables/.github/workflows/terraform-run.yml@main
    with:
      environment: dev
      region: eu-west-1
      action: plan
      working-directory: terraform
      skip-terraform-report: true
    secrets: inherit
```

## lambda-build.yml

Detects Lambda functions (Python or Node.js) under the working directory and builds each one. Detects changed directories via git diff for incremental builds unless `full-rebuild` is enabled. Builds function packages (code only) and layer packages (dependencies), then uploads all `.zip` artifacts to the shared S3 artifacts bucket so Terraform can use them.

### Inputs

| Input | Type | Default | Required | Description |
| --- | --- | --- | --- | --- |
| `environment` | string | - | Yes | The target environment (dev, stg, prod), required when s3-bucket is provided |
| `environment-prefix` | string | `''` | No | Optional prefix for the environment name (e.g., `myorg-`). When set, it is prepended to the environment name and used in the Terraform workspace and Project tag. |
| `region` | string | - | Yes | AWS region for the S3 bucket |
| `working-directory` | string | `.` | No | The location containing the lambda subfolders to build |
| `full-rebuild` | boolean | `false` | No | Whether to perform a full rebuild of all lambdas (true/false) |

### Example

```yaml
jobs:
  lambdas:
    uses: faccomichele/gha-reusables/.github/workflows/lambda-build.yml@main
    with:
      environment: dev
      region: eu-west-1
      working-directory: lambdas
      full-rebuild: true
    secrets: inherit
```

## ecr-docker-build.yml

Recursively detects Dockerfiles under the working directory and builds each selected image with Buildx, tagging with `latest`, a date tag, and the commit SHA. Each Dockerfile gets its own ECR repository using its normalized path as the repository suffix. Builds use the repository root as the Docker context, so Dockerfiles can copy files from other project directories and the root `.dockerignore` is honored. Optionally pushes to ECR (creating the repository if it does not exist).

When `full-rebuild` is `true`, every Dockerfile is built. When it is `false`, a Dockerfile without a sibling `dependencies.txt` is also always built. For a Dockerfile with a sibling `dependencies.txt`, the Dockerfile is built when the Dockerfile or manifest changes, or when a changed repository-root-relative path matches an entry in the manifest. Entries ending in `/` match recursively beneath that directory; all other entries match one exact file. An optional leading `/` is ignored, so `include.dll` matches `include.dll`, `/src/include.dll` matches `src/include.dll`, and `/src/` matches files below `src/`. Blank lines are ignored and entries do not support glob patterns. The workflow uses the full push range when available and falls back to the previous commit; if no changed-file range can be determined, manifest-backed Dockerfiles are skipped while Dockerfiles without manifests still build.

### Inputs

| Input | Type | Default | Required | Description |
| --- | --- | --- | --- | --- |
| `environment` | string | - | Yes | The target environment (dev, stg, prod), required when s3-bucket is provided |
| `environment-prefix` | string | `''` | No | Optional prefix for the environment name (e.g., `myorg-`). When set, it is prepended to the environment name and used in the Terraform workspace and Project tag. |
| `region` | string | - | Yes | AWS region for the S3 bucket |
| `working-directory` | string | `.` | No | The directory to scan recursively for Dockerfiles |
| `full-rebuild` | boolean | `false` | No | Whether to build every detected Dockerfile, ignoring `dependencies.txt` filtering |
| `push-to-ecr` | boolean | `true` | No | A switch that sets whether to push the built image to ECR or not |

### Repository variables

- `ECR_REGISTRY` - optional ECR registry override; when unset, the registry from the AWS login is used
- `DOCKER_BUILD_ARGS` - optional comma-separated build args passed to the Docker build

### Example

```yaml
jobs:
  build:
    uses: faccomichele/gha-reusables/.github/workflows/ecr-docker-build.yml@main
    with:
      environment: dev
      region: eu-west-1
      working-directory: services
      push-to-ecr: true
    secrets: inherit
```

## deploy-website.yml

Downloads the Terraform output artifact produced by `terraform-run.yml` (with `upload-output-artifact: true`), replaces `__KEY__` placeholders in the working directory files with values resolved from SSM parameters when the Terraform output value is a parameter name (otherwise the raw Terraform output value is used), syncs the files to the website S3 bucket, and creates a CloudFront invalidation (all paths or only changed paths).

### Inputs

| Input | Type | Default | Required | Description |
| --- | --- | --- | --- | --- |
| `environment` | string | - | Yes | The target environment (dev, stg, prod), required when s3-bucket is provided |
| `environment-prefix` | string | `''` | No | Optional prefix for the environment name (e.g., `myorg-`). When set, it is prepended to the environment name and used in the Terraform workspace and Project tag. |
| `region` | string | - | Yes | AWS region for the S3 bucket |
| `working-directory` | string | `.` | No | The location containing the files to be synced to S3 |

### Terraform outputs

The workflow expects the Terraform state to expose outputs named `website_bucket_name` and `cloudfront_distribution_id`. If those output values refer to SSM parameters, they are resolved first; otherwise their raw Terraform output values are used directly for the S3 sync and CloudFront invalidation steps.

### Example

```yaml
jobs:
  deploy:
    uses: faccomichele/gha-reusables/.github/workflows/deploy-website.yml@main
    with:
      environment: dev
      region: eu-west-1
      working-directory: website
    secrets: inherit
```

## delete-labels.yml

Deletes all labels from the repository. Requires the `issues: write` permission.

### Example

```yaml
jobs:
  cleanup:
    uses: faccomichele/gha-reusables/.github/workflows/delete-labels.yml@main
```

## new-repo.yml

Manually triggered workflow (`workflow_dispatch`) that prepares a repository for the initial sync of the IaC configuration. It runs a destroy to ensure that any existing resources that might conflict with the Terraform configuration are removed before the first apply. This is especially useful when converting an existing repository with manual setup into being fully managed by Terraform.

### Example

```yaml
on:
  workflow_dispatch:

jobs:
  main:
    uses: faccomichele/gha-reusables/.github/workflows/new-repo.yml@main
```

## Notes

- All workflows run on `${{ vars.RUNNERS_AMD64 || 'ubuntu-latest' }}` by default; set the `RUNNERS_AMD64` repository variable to override the runner.
- AWS authentication is handled by the custom AWS login composite (`faccomichele/gha-composite-custom-aws-login`), which supports both STS-assume-role and static access key flows.
- Pin workflows to a release tag instead of `main` for production use.
