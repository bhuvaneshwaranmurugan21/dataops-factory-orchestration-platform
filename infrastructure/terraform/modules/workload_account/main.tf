data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.project_name}-${var.account_label}-artifacts-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "standard_job" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "jobs/standard_transform.py"
  source = "${path.root}/../../spark_jobs/standard_transform.py"
  etag   = filemd5("${path.root}/../../spark_jobs/standard_transform.py")
}

resource "aws_s3_object" "heavy_job" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "jobs/heavy_transform.py"
  source = "${path.root}/../../spark_jobs/heavy_transform.py"
  etag   = filemd5("${path.root}/../../spark_jobs/heavy_transform.py")
}

resource "aws_s3_object" "spark_bundle" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "jobs/spark_jobs.zip"
  source = "${path.root}/../../dist/spark_jobs/spark_jobs.zip"
  etag   = filemd5("${path.root}/../../dist/spark_jobs/spark_jobs.zip")
}

resource "aws_kms_key" "data" {
  description             = "${var.project_name} ${var.account_label} data encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.data_key.json
  depends_on              = [aws_iam_role.runtime, aws_iam_role.execution]
}

resource "aws_kms_key" "signer" {
  description              = "${var.project_name} ${var.account_label} manifest signer"
  customer_master_key_spec = "ECC_NIST_P256"
  key_usage                = "SIGN_VERIFY"
  deletion_window_in_days  = 7
  policy                   = data.aws_iam_policy_document.signer_key.json
  depends_on               = [aws_iam_role.runtime]
}

data "aws_iam_policy_document" "data_key" {
  statement {
    sid       = "AccountAdministration"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
  statement {
    sid       = "WorkloadEncryptControlRead"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = ["*"]
    principals {
      type = "AWS"
      identifiers = [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/dataops-workload-executor",
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project_name}-${var.account_label}-runtime",
        var.control_execution_role_arn,
      ]
    }
  }
}

data "aws_iam_policy_document" "signer_key" {
  statement {
    sid       = "AccountAdministration"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
  statement {
    sid       = "WorkloadSign"
    actions   = ["kms:Sign", "kms:GetPublicKey", "kms:DescribeKey"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project_name}-${var.account_label}-runtime"]
    }
  }
  statement {
    sid       = "ControlVerify"
    actions   = ["kms:Verify", "kms:GetPublicKey", "kms:DescribeKey"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [var.control_execution_role_arn]
    }
  }
}

resource "aws_s3_bucket" "manifests" {
  bucket              = "${var.project_name}-${var.account_label}-${data.aws_caller_identity.current.account_id}"
  object_lock_enabled = true
}

resource "aws_s3_bucket_versioning" "manifests" {
  bucket = aws_s3_bucket.manifests.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_object_lock_configuration" "manifests" {
  bucket = aws_s3_bucket.manifests.id
  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = 7
    }
  }
  depends_on = [aws_s3_bucket_versioning.manifests]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "manifests" {
  bucket = aws_s3_bucket.manifests.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.data.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "manifests" {
  bucket                  = aws_s3_bucket.manifests.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "manifest_bucket" {
  statement {
    sid = "ControlPlaneReadLockedManifests"
    principals {
      type        = "AWS"
      identifiers = [var.control_execution_role_arn]
    }
    actions = [
      "s3:GetObjectVersion",
      "s3:GetObjectRetention",
    ]
    resources = ["${aws_s3_bucket.manifests.arn}/*"]
  }
}

resource "aws_s3_bucket_policy" "manifests" {
  bucket = aws_s3_bucket.manifests.id
  policy = data.aws_iam_policy_document.manifest_bucket.json
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [var.control_execution_role_arn]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "dataops-workload-executor"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

data "aws_iam_policy_document" "runtime_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com", "glue.amazonaws.com", "emr-serverless.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "runtime" {
  name               = "${var.project_name}-${var.account_label}-runtime"
  assume_role_policy = data.aws_iam_policy_document.runtime_trust.json
}

data "aws_iam_policy_document" "runtime" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"]
  }
  statement {
    sid = "ReadArtifactsWriteOutputs"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:ListBucket",
      "s3:PutObject",
      "s3:PutObjectRetention",
    ]
    resources = [
      aws_s3_bucket.artifacts.arn,
      "${aws_s3_bucket.artifacts.arn}/*",
      aws_s3_bucket.manifests.arn,
      "${aws_s3_bucket.manifests.arn}/*",
    ]
  }
  statement {
    sid       = "EncryptAndSign"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:Sign"]
    resources = [aws_kms_key.data.arn, aws_kms_key.signer.arn]
  }
}

resource "aws_iam_role_policy" "runtime" {
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime.json
}

resource "aws_lambda_function" "workload" {
  function_name    = "${var.project_name}-${var.account_label}-workload"
  filename         = "${var.lambda_artifact_dir}/workload.zip"
  source_code_hash = filebase64sha256("${var.lambda_artifact_dir}/workload.zip")
  role             = aws_iam_role.runtime.arn
  runtime          = "python3.11"
  handler          = "handler.handler"
  timeout          = 60
  memory_size      = 256
}

resource "aws_glue_job" "standard" {
  name              = "${var.project_name}-${var.account_label}-standard"
  role_arn          = aws_iam_role.runtime.arn
  glue_version      = "5.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 60
  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_object.standard_job.key}"
  }
  default_arguments = {
    "--extra-py-files"                   = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_object.spark_bundle.key}"
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
    "--enable-observability-metrics"     = "true"
    "--job-bookmark-option"              = "job-bookmark-disable"
  }
}

resource "aws_emrserverless_application" "heavy" {
  name          = "${var.project_name}-${var.account_label}-heavy"
  release_label = var.emr_release_label
  type          = "SPARK"
  auto_stop_configuration {
    enabled              = true
    idle_timeout_minutes = 5
  }
  maximum_capacity {
    cpu    = "8 vCPU"
    memory = "32 GB"
    disk   = "80 GB"
  }
}

data "aws_iam_policy_document" "execution" {
  statement {
    sid = "ReadManifestsForReconciliation"
    actions = [
      "s3:GetObjectVersion",
      "s3:GetObjectRetention",
    ]
    resources = ["${aws_s3_bucket.manifests.arn}/*"]
  }

  statement {
    sid       = "DecryptManifestMetadata"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.data.arn]
  }

  statement {
    sid = "GovernedEngines"
    actions = [
      "lambda:InvokeFunction",
      "glue:StartJobRun",
      "glue:GetJobRun",
      "glue:GetJobRuns",
      "emr-serverless:StartJobRun",
      "emr-serverless:GetJobRun",
      "emr-serverless:CancelJobRun",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = [var.project_name]
    }
  }

  statement {
    sid       = "PassGovernedRuntimeRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.runtime.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["glue.amazonaws.com", "emr-serverless.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "execution" {
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}
