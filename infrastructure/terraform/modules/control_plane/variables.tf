variable "project_name" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "lambda_artifact_dir" {
  type = string
}

variable "pipeline_id" {
  type = string
}

variable "prepublication_job_ids" {
  type = list(string)
}

variable "workload_execution_profiles" {
  type = map(object({
    adapter_type       = string
    role_arn           = string
    region             = string
    target             = string
    account_label      = string
    artifact_bucket    = string
    manifest_bucket    = string
    signer_key_arn     = string
    execution_role_arn = optional(string)
  }))
}

variable "workload_role_arns" {
  type = list(string)
}

variable "workload_manifest_bucket_arns" {
  type = list(string)
}

variable "workload_manifest_bucket_names" {
  type = list(string)
}

variable "workload_signer_key_arns" {
  type = list(string)
}

variable "workload_data_key_arns" {
  type = list(string)
}
