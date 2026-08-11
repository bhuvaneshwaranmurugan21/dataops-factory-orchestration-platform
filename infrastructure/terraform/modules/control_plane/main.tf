data "aws_caller_identity" "current" {}

resource "aws_kms_key" "control" {
  description             = "${var.project_name} control-plane encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_key" "signer" {
  description              = "${var.project_name} control manifest signer"
  customer_master_key_spec = "ECC_NIST_P256"
  key_usage                = "SIGN_VERIFY"
  deletion_window_in_days  = 7
}

resource "aws_dynamodb_table" "run_ledger" {
  name         = "${var.project_name}-run-ledger"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.control.arn
  }
}

resource "aws_dynamodb_table" "callbacks" {
  name         = "${var.project_name}-callbacks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "dispatch_key"

  attribute {
    name = "dispatch_key"
    type = "S"
  }
  attribute {
    name = "state"
    type = "S"
  }
  attribute {
    name = "updated_at"
    type = "N"
  }

  global_secondary_index {
    name            = "state-updated-index"
    hash_key        = "state"
    range_key       = "updated_at"
    projection_type = "KEYS_ONLY"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.control.arn
  }
}

resource "aws_s3_bucket" "plans" {
  bucket              = "${var.project_name}-plans-${data.aws_caller_identity.current.account_id}"
  object_lock_enabled = true
}

resource "aws_s3_bucket_versioning" "plans" {
  bucket = aws_s3_bucket.plans.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_object_lock_configuration" "plans" {
  bucket = aws_s3_bucket.plans.id
  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = 7
    }
  }
  depends_on = [aws_s3_bucket_versioning.plans]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "plans" {
  bucket = aws_s3_bucket.plans.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.control.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "plans" {
  bucket                  = aws_s3_bucket.plans.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.project_name}-control-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "control_invoker_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.lambda.arn]
    }
  }
}

resource "aws_iam_role" "control_invoker" {
  name               = "${var.project_name}-control-invoker"
  assume_role_policy = data.aws_iam_policy_document.control_invoker_trust.json
}

data "aws_iam_policy_document" "control_workload_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "control_workload" {
  name               = "${var.project_name}-control-workload"
  assume_role_policy = data.aws_iam_policy_document.control_workload_trust.json
}

resource "aws_iam_role" "publication" {
  name               = "${var.project_name}-publication"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"]
  }

  statement {
    sid       = "SignControlManifests"
    actions   = ["kms:Sign"]
    resources = [aws_kms_key.signer.arn]
  }

  statement {
    sid = "LedgerAndCallbacks"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:TransactWriteItems",
    ]
    resources = [
      aws_dynamodb_table.run_ledger.arn,
      aws_dynamodb_table.callbacks.arn,
      "${aws_dynamodb_table.callbacks.arn}/index/state-updated-index",
    ]
  }

  statement {
    sid       = "AssumeWorkloadRoles"
    actions   = ["sts:AssumeRole"]
    resources = concat(var.workload_role_arns, [aws_iam_role.control_invoker.arn])
  }

  statement {
    sid     = "ReadLockedManifests"
    actions = ["s3:GetObjectVersion", "s3:GetObjectRetention"]
    resources = concat(
      [for arn in var.workload_manifest_bucket_arns : "${arn}/*"],
      ["${aws_s3_bucket.plans.arn}/*"],
    )
  }

  statement {
    sid       = "DecryptAndVerifyManifests"
    actions   = ["kms:Decrypt", "kms:Verify", "kms:GetPublicKey"]
    resources = concat(var.workload_data_key_arns, var.workload_signer_key_arns, [aws_kms_key.signer.arn])
  }

  statement {
    sid       = "StepFunctionCallbacks"
    actions   = ["states:SendTaskSuccess", "states:SendTaskFailure", "states:SendTaskHeartbeat"]
    resources = ["*"]
  }

  statement {
    sid       = "InvokeReconciler"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-*"]
  }
}

resource "aws_iam_role_policy" "lambda" {
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

data "aws_iam_policy_document" "control_invoker" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.control_workload.arn, aws_lambda_function.publish_catalog.arn]
  }
}

resource "aws_iam_role_policy" "control_invoker" {
  role   = aws_iam_role.control_invoker.id
  policy = data.aws_iam_policy_document.control_invoker.json
}

data "aws_iam_policy_document" "control_workload" {
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"]
  }
  statement {
    actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:PutObjectRetention"]
    resources = ["${aws_s3_bucket.plans.arn}/*"]
  }
  statement {
    actions = ["kms:Sign", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [
      aws_kms_key.signer.arn,
      aws_kms_key.control.arn,
    ]
  }
}

resource "aws_iam_role_policy" "control_workload" {
  role   = aws_iam_role.control_workload.id
  policy = data.aws_iam_policy_document.control_workload.json
}

resource "aws_redshiftserverless_namespace" "catalog" {
  namespace_name                   = "${var.project_name}-catalog"
  db_name                          = "dataops"
  admin_username                   = "dataops_admin"
  manage_admin_password            = true
  admin_password_secret_kms_key_id = aws_kms_key.control.arn
  kms_key_id                       = aws_kms_key.control.arn
  log_exports                      = ["useractivitylog", "userlog"]
}

resource "aws_redshiftserverless_workgroup" "catalog" {
  workgroup_name       = "${var.project_name}-catalog"
  namespace_name       = aws_redshiftserverless_namespace.catalog.namespace_name
  base_capacity        = 8
  enhanced_vpc_routing = true
  publicly_accessible  = false
}

data "aws_iam_policy_document" "publication" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"]
  }
  statement {
    sid       = "ReadAcceptedManifests"
    actions   = ["dynamodb:Query", "dynamodb:GetItem"]
    resources = [aws_dynamodb_table.run_ledger.arn]
  }
  statement {
    sid       = "WritePublicationEvidence"
    actions   = ["s3:PutObject", "s3:PutObjectRetention"]
    resources = ["${aws_s3_bucket.plans.arn}/*"]
  }
  statement {
    sid       = "EncryptAndSignPublication"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:Sign"]
    resources = [aws_kms_key.control.arn, aws_kms_key.signer.arn]
  }
  statement {
    sid = "UseRedshiftDataApi"
    actions = [
      "redshift-data:ExecuteStatement",
      "redshift-data:DescribeStatement",
      "redshift-data:GetStatementResult",
      "redshift-serverless:GetCredentials",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "ReadManagedRedshiftCredential"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_redshiftserverless_namespace.catalog.admin_password_secret_arn]
  }
}

resource "aws_iam_role_policy" "publication" {
  role   = aws_iam_role.publication.id
  policy = data.aws_iam_policy_document.publication.json
}

locals {
  common_lambda = {
    role          = aws_iam_role.lambda.arn
    runtime       = "python3.11"
    handler       = "handler.handler"
    timeout       = 60
    memory_size   = 256
    architectures = ["x86_64"]
  }
}

resource "aws_lambda_function" "register_run" {
  function_name    = "${var.project_name}-register-run"
  filename         = "${var.lambda_artifact_dir}/register_run.zip"
  source_code_hash = filebase64sha256("${var.lambda_artifact_dir}/register_run.zip")
  role             = local.common_lambda.role
  runtime          = local.common_lambda.runtime
  handler          = local.common_lambda.handler
  timeout          = local.common_lambda.timeout
  memory_size      = local.common_lambda.memory_size
  architectures    = local.common_lambda.architectures
  environment {
    variables = { RUN_LEDGER_TABLE = aws_dynamodb_table.run_ledger.name, RUN_LEASE_SECONDS = "300" }
  }
}

resource "aws_lambda_function" "control_workload" {
  function_name    = "${var.project_name}-control-workload"
  filename         = "${var.lambda_artifact_dir}/control_workload.zip"
  source_code_hash = filebase64sha256("${var.lambda_artifact_dir}/control_workload.zip")
  role             = aws_iam_role.control_workload.arn
  runtime          = local.common_lambda.runtime
  handler          = local.common_lambda.handler
  timeout          = local.common_lambda.timeout
  memory_size      = local.common_lambda.memory_size
  architectures    = local.common_lambda.architectures
  environment {
    variables = {
      CONTROL_JOB_ALLOWLIST = jsonencode([
        "register_snapshot",
        "validate_contracts",
        "open_run_budget",
        "validate_required_branches",
        "reconcile_lineage",
        "reconcile_costs",
        "prepare_publication",
        "validate_manifest_set",
      ])
      CONTROL_MANIFEST_BUCKET = aws_s3_bucket.plans.id
      CONTROL_SIGNER_KEY_ARN  = aws_kms_key.signer.arn
    }
  }
}

resource "aws_lambda_function" "publish_catalog" {
  function_name    = "${var.project_name}-publish-catalog"
  filename         = "${var.lambda_artifact_dir}/publish_catalog.zip"
  source_code_hash = filebase64sha256("${var.lambda_artifact_dir}/publish_catalog.zip")
  role             = aws_iam_role.publication.arn
  runtime          = local.common_lambda.runtime
  handler          = local.common_lambda.handler
  timeout          = 180
  memory_size      = local.common_lambda.memory_size
  architectures    = local.common_lambda.architectures
  environment {
    variables = {
      RUN_LEDGER_TABLE             = aws_dynamodb_table.run_ledger.name
      REQUIRED_PREPUBLICATION_JOBS = jsonencode(var.prepublication_job_ids)
      REDSHIFT_WORKGROUP_NAME      = aws_redshiftserverless_workgroup.catalog.workgroup_name
      REDSHIFT_DATABASE            = aws_redshiftserverless_namespace.catalog.db_name
      REDSHIFT_SECRET_ARN          = aws_redshiftserverless_namespace.catalog.admin_password_secret_arn
      PIPELINE_ID                  = var.pipeline_id
      CONTROL_MANIFEST_BUCKET      = aws_s3_bucket.plans.id
      CONTROL_SIGNER_KEY_ARN       = aws_kms_key.signer.arn
    }
  }
}

locals {
  execution_profiles = merge(
    var.workload_execution_profiles,
    {
      control-lambda-v1 = {
        adapter_type       = "lambda"
        role_arn           = aws_iam_role.control_invoker.arn
        region             = var.aws_region
        target             = aws_lambda_function.control_workload.arn
        account_label      = "control-plane"
        artifact_bucket    = aws_s3_bucket.plans.id
        manifest_bucket    = aws_s3_bucket.plans.id
        signer_key_arn     = aws_kms_key.signer.arn
        execution_role_arn = null
      }
      control-publication-v1 = {
        adapter_type       = "lambda"
        role_arn           = aws_iam_role.control_invoker.arn
        region             = var.aws_region
        target             = aws_lambda_function.publish_catalog.arn
        account_label      = "control-plane"
        artifact_bucket    = aws_s3_bucket.plans.id
        manifest_bucket    = aws_s3_bucket.plans.id
        signer_key_arn     = aws_kms_key.signer.arn
        execution_role_arn = null
      }
    },
  )
}

resource "aws_lambda_function" "dispatch" {
  function_name    = "${var.project_name}-dispatch"
  filename         = "${var.lambda_artifact_dir}/dispatch.zip"
  source_code_hash = filebase64sha256("${var.lambda_artifact_dir}/dispatch.zip")
  role             = local.common_lambda.role
  runtime          = local.common_lambda.runtime
  handler          = local.common_lambda.handler
  timeout          = local.common_lambda.timeout
  memory_size      = local.common_lambda.memory_size
  architectures    = local.common_lambda.architectures
  environment {
    variables = {
      CALLBACK_TABLE             = aws_dynamodb_table.callbacks.name
      CALLBACK_RETENTION_SECONDS = "604800"
      EXECUTION_PROFILES         = jsonencode(local.execution_profiles)
      RUN_LEDGER_TABLE           = aws_dynamodb_table.run_ledger.name
      RUN_LEASE_SECONDS          = "300"
    }
  }
}

resource "aws_lambda_function" "reconcile" {
  function_name    = "${var.project_name}-reconcile"
  filename         = "${var.lambda_artifact_dir}/reconcile.zip"
  source_code_hash = filebase64sha256("${var.lambda_artifact_dir}/reconcile.zip")
  role             = local.common_lambda.role
  runtime          = local.common_lambda.runtime
  handler          = local.common_lambda.handler
  timeout          = local.common_lambda.timeout
  memory_size      = local.common_lambda.memory_size
  architectures    = local.common_lambda.architectures
  environment {
    variables = {
      CALLBACK_TABLE     = aws_dynamodb_table.callbacks.name
      EXECUTION_PROFILES = jsonencode(local.execution_profiles)
      RUN_LEDGER_TABLE   = aws_dynamodb_table.run_ledger.name
      RUN_LEASE_SECONDS  = "300"
    }
  }
}

resource "aws_lambda_function" "reconcile_sweep" {
  function_name    = "${var.project_name}-reconcile-sweep"
  filename         = "${var.lambda_artifact_dir}/reconcile_sweep.zip"
  source_code_hash = filebase64sha256("${var.lambda_artifact_dir}/reconcile_sweep.zip")
  role             = local.common_lambda.role
  runtime          = local.common_lambda.runtime
  handler          = local.common_lambda.handler
  timeout          = local.common_lambda.timeout
  memory_size      = local.common_lambda.memory_size
  architectures    = local.common_lambda.architectures
  environment {
    variables = {
      CALLBACK_TABLE       = aws_dynamodb_table.callbacks.name
      RECONCILE_FUNCTION   = aws_lambda_function.reconcile.function_name
      RECONCILE_BATCH_SIZE = "100"
    }
  }
}

resource "aws_lambda_function" "validate_manifest" {
  function_name    = "${var.project_name}-validate-manifest"
  filename         = "${var.lambda_artifact_dir}/validate_manifest.zip"
  source_code_hash = filebase64sha256("${var.lambda_artifact_dir}/validate_manifest.zip")
  role             = local.common_lambda.role
  runtime          = local.common_lambda.runtime
  handler          = local.common_lambda.handler
  timeout          = local.common_lambda.timeout
  memory_size      = local.common_lambda.memory_size
  architectures    = local.common_lambda.architectures
  environment {
    variables = {
      MANIFEST_BUCKETS   = jsonencode(concat(var.workload_manifest_bucket_names, [aws_s3_bucket.plans.id]))
      SIGNER_KEY_ARNS    = jsonencode(concat(var.workload_signer_key_arns, [aws_kms_key.signer.arn]))
      EXECUTION_PROFILES = jsonencode(local.execution_profiles)
      RUN_LEDGER_TABLE   = aws_dynamodb_table.run_ledger.name
    }
  }
}

resource "aws_lambda_function" "mark_failed" {
  function_name    = "${var.project_name}-mark-failed"
  filename         = "${var.lambda_artifact_dir}/mark_failed.zip"
  source_code_hash = filebase64sha256("${var.lambda_artifact_dir}/mark_failed.zip")
  role             = local.common_lambda.role
  runtime          = local.common_lambda.runtime
  handler          = local.common_lambda.handler
  timeout          = local.common_lambda.timeout
  memory_size      = local.common_lambda.memory_size
  architectures    = local.common_lambda.architectures
  environment {
    variables = { RUN_LEDGER_TABLE = aws_dynamodb_table.run_ledger.name }
  }
}

data "aws_iam_policy_document" "step_functions_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "step_functions" {
  name               = "${var.project_name}-step-functions"
  assume_role_policy = data.aws_iam_policy_document.step_functions_trust.json
}

data "aws_iam_policy_document" "step_functions" {
  statement {
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.dispatch.arn,
      aws_lambda_function.validate_manifest.arn,
      aws_lambda_function.mark_failed.arn,
    ]
  }
  statement {
    actions   = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "step_functions" {
  role   = aws_iam_role.step_functions.id
  policy = data.aws_iam_policy_document.step_functions.json
}

resource "aws_cloudwatch_log_group" "step_functions" {
  name              = "/aws/vendedlogs/states/${var.project_name}"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.control.arn
}

resource "aws_sfn_state_machine" "workload" {
  name     = "${var.project_name}-workload"
  role_arn = aws_iam_role.step_functions.arn
  type     = "STANDARD"
  definition = templatefile("${path.root}/../../orchestration/step_functions/workload_execution.asl.json", {
    dispatch_lambda_arn          = aws_lambda_function.dispatch.arn
    validate_manifest_lambda_arn = aws_lambda_function.validate_manifest.arn
    mark_failed_lambda_arn       = aws_lambda_function.mark_failed.arn
  })
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.step_functions.arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }
  tracing_configuration { enabled = true }
}

resource "aws_cloudwatch_event_rule" "reconcile" {
  name                = "${var.project_name}-reconcile"
  schedule_expression = "rate(1 minute)"
}

resource "aws_cloudwatch_event_target" "reconcile" {
  rule = aws_cloudwatch_event_rule.reconcile.name
  arn  = aws_lambda_function.reconcile_sweep.arn
}

resource "aws_lambda_permission" "reconcile" {
  statement_id  = "AllowEventBridgeReconcile"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.reconcile_sweep.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reconcile.arn
}

resource "aws_cloudwatch_metric_alarm" "failed_executions" {
  alarm_name          = "${var.project_name}-failed-executions"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.workload.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
}
