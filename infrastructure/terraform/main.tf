locals {
  control_execution_role_arn = "arn:aws:iam::${var.control_account_id}:role/${var.project_name}-control-lambda"
  compiled_plan              = jsondecode(file("${path.root}/../../orchestration/generated/compiled-plan.json"))
  prepublication_job_ids = [
    for job in local.compiled_plan.jobs : job.job_id
    if job.mandatory && !contains(["activate_catalog", "audit_publication"], job.job_id)
  ]
}

module "commerce_workload" {
  source = "./modules/workload_account"
  providers = { aws = aws.commerce }

  project_name               = var.project_name
  account_label              = "commerce"
  control_execution_role_arn = local.control_execution_role_arn
  aws_region                 = var.aws_region
  lambda_artifact_dir        = var.lambda_artifact_dir
  emr_release_label          = var.emr_release_label
}

module "finance_workload" {
  source = "./modules/workload_account"
  providers = { aws = aws.finance }

  project_name               = var.project_name
  account_label              = "finance"
  control_execution_role_arn = local.control_execution_role_arn
  aws_region                 = var.aws_region
  lambda_artifact_dir        = var.lambda_artifact_dir
  emr_release_label          = var.emr_release_label
}

module "customer_workload" {
  source = "./modules/workload_account"
  providers = { aws = aws.customer }

  project_name               = var.project_name
  account_label              = "customer"
  control_execution_role_arn = local.control_execution_role_arn
  aws_region                 = var.aws_region
  lambda_artifact_dir        = var.lambda_artifact_dir
  emr_release_label          = var.emr_release_label
}

module "operations_workload" {
  source = "./modules/workload_account"
  providers = { aws = aws.operations }

  project_name               = var.project_name
  account_label              = "operations"
  control_execution_role_arn = local.control_execution_role_arn
  aws_region                 = var.aws_region
  lambda_artifact_dir        = var.lambda_artifact_dir
  emr_release_label          = var.emr_release_label
}

locals {
  workload_execution_profiles = {
    commerce-lambda-v1 = {
      adapter_type      = "lambda"
      role_arn          = module.commerce_workload.execution_role_arn
      region            = var.aws_region
      target            = module.commerce_workload.lambda_function_arn
      account_label     = "commerce-data"
      artifact_bucket   = module.commerce_workload.artifact_bucket_name
      manifest_bucket   = module.commerce_workload.manifest_bucket_name
      signer_key_arn    = module.commerce_workload.signer_key_arn
      execution_role_arn = null
    }
    commerce-glue-v1 = {
      adapter_type      = "glue"
      role_arn          = module.commerce_workload.execution_role_arn
      region            = var.aws_region
      target            = module.commerce_workload.glue_job_name
      account_label     = "commerce-data"
      artifact_bucket   = module.commerce_workload.artifact_bucket_name
      manifest_bucket   = module.commerce_workload.manifest_bucket_name
      signer_key_arn    = module.commerce_workload.signer_key_arn
      execution_role_arn = null
    }
    commerce-emr-v1 = {
      adapter_type       = "emr_serverless"
      role_arn           = module.commerce_workload.execution_role_arn
      region             = var.aws_region
      target             = module.commerce_workload.emr_application_id
      account_label      = "commerce-data"
      artifact_bucket    = module.commerce_workload.artifact_bucket_name
      manifest_bucket    = module.commerce_workload.manifest_bucket_name
      signer_key_arn     = module.commerce_workload.signer_key_arn
      execution_role_arn = module.commerce_workload.runtime_role_arn
    }
    finance-lambda-v1 = {
      adapter_type      = "lambda"
      role_arn          = module.finance_workload.execution_role_arn
      region            = var.aws_region
      target            = module.finance_workload.lambda_function_arn
      account_label     = "finance-data"
      artifact_bucket   = module.finance_workload.artifact_bucket_name
      manifest_bucket   = module.finance_workload.manifest_bucket_name
      signer_key_arn    = module.finance_workload.signer_key_arn
      execution_role_arn = null
    }
    finance-glue-v1 = {
      adapter_type      = "glue"
      role_arn          = module.finance_workload.execution_role_arn
      region            = var.aws_region
      target            = module.finance_workload.glue_job_name
      account_label     = "finance-data"
      artifact_bucket   = module.finance_workload.artifact_bucket_name
      manifest_bucket   = module.finance_workload.manifest_bucket_name
      signer_key_arn    = module.finance_workload.signer_key_arn
      execution_role_arn = null
    }
    finance-emr-v1 = {
      adapter_type       = "emr_serverless"
      role_arn           = module.finance_workload.execution_role_arn
      region             = var.aws_region
      target             = module.finance_workload.emr_application_id
      account_label      = "finance-data"
      artifact_bucket    = module.finance_workload.artifact_bucket_name
      manifest_bucket    = module.finance_workload.manifest_bucket_name
      signer_key_arn     = module.finance_workload.signer_key_arn
      execution_role_arn = module.finance_workload.runtime_role_arn
    }
    customer-lambda-v1 = {
      adapter_type      = "lambda"
      role_arn          = module.customer_workload.execution_role_arn
      region            = var.aws_region
      target            = module.customer_workload.lambda_function_arn
      account_label     = "customer-data"
      artifact_bucket   = module.customer_workload.artifact_bucket_name
      manifest_bucket   = module.customer_workload.manifest_bucket_name
      signer_key_arn    = module.customer_workload.signer_key_arn
      execution_role_arn = null
    }
    customer-glue-v1 = {
      adapter_type      = "glue"
      role_arn          = module.customer_workload.execution_role_arn
      region            = var.aws_region
      target            = module.customer_workload.glue_job_name
      account_label     = "customer-data"
      artifact_bucket   = module.customer_workload.artifact_bucket_name
      manifest_bucket   = module.customer_workload.manifest_bucket_name
      signer_key_arn    = module.customer_workload.signer_key_arn
      execution_role_arn = null
    }
    customer-emr-v1 = {
      adapter_type       = "emr_serverless"
      role_arn           = module.customer_workload.execution_role_arn
      region             = var.aws_region
      target             = module.customer_workload.emr_application_id
      account_label      = "customer-data"
      artifact_bucket    = module.customer_workload.artifact_bucket_name
      manifest_bucket    = module.customer_workload.manifest_bucket_name
      signer_key_arn     = module.customer_workload.signer_key_arn
      execution_role_arn = module.customer_workload.runtime_role_arn
    }
    operations-lambda-v1 = {
      adapter_type      = "lambda"
      role_arn          = module.operations_workload.execution_role_arn
      region            = var.aws_region
      target            = module.operations_workload.lambda_function_arn
      account_label     = "operations-data"
      artifact_bucket   = module.operations_workload.artifact_bucket_name
      manifest_bucket   = module.operations_workload.manifest_bucket_name
      signer_key_arn    = module.operations_workload.signer_key_arn
      execution_role_arn = null
    }
    operations-glue-v1 = {
      adapter_type      = "glue"
      role_arn          = module.operations_workload.execution_role_arn
      region            = var.aws_region
      target            = module.operations_workload.glue_job_name
      account_label     = "operations-data"
      artifact_bucket   = module.operations_workload.artifact_bucket_name
      manifest_bucket   = module.operations_workload.manifest_bucket_name
      signer_key_arn    = module.operations_workload.signer_key_arn
      execution_role_arn = null
    }
    operations-emr-v1 = {
      adapter_type       = "emr_serverless"
      role_arn           = module.operations_workload.execution_role_arn
      region             = var.aws_region
      target             = module.operations_workload.emr_application_id
      account_label      = "operations-data"
      artifact_bucket    = module.operations_workload.artifact_bucket_name
      manifest_bucket    = module.operations_workload.manifest_bucket_name
      signer_key_arn     = module.operations_workload.signer_key_arn
      execution_role_arn = module.operations_workload.runtime_role_arn
    }
  }
}

module "control_plane" {
  source = "./modules/control_plane"
  providers = { aws = aws.control }

  project_name            = var.project_name
  pipeline_id             = local.compiled_plan.pipeline_id
  aws_region              = var.aws_region
  lambda_artifact_dir     = var.lambda_artifact_dir
  prepublication_job_ids  = local.prepublication_job_ids
  workload_execution_profiles = local.workload_execution_profiles
  workload_role_arns = [
    module.commerce_workload.execution_role_arn,
    module.finance_workload.execution_role_arn,
    module.customer_workload.execution_role_arn,
    module.operations_workload.execution_role_arn,
  ]
  workload_manifest_bucket_arns = [
    module.commerce_workload.manifest_bucket_arn,
    module.finance_workload.manifest_bucket_arn,
    module.customer_workload.manifest_bucket_arn,
    module.operations_workload.manifest_bucket_arn,
  ]
  workload_manifest_bucket_names = [
    module.commerce_workload.manifest_bucket_name,
    module.finance_workload.manifest_bucket_name,
    module.customer_workload.manifest_bucket_name,
    module.operations_workload.manifest_bucket_name,
  ]
  workload_signer_key_arns = [
    module.commerce_workload.signer_key_arn,
    module.finance_workload.signer_key_arn,
    module.customer_workload.signer_key_arn,
    module.operations_workload.signer_key_arn,
  ]
  workload_data_key_arns = [
    module.commerce_workload.data_key_arn,
    module.finance_workload.data_key_arn,
    module.customer_workload.data_key_arn,
    module.operations_workload.data_key_arn,
  ]
}
