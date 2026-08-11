variable "aws_region" {
  description = "AWS region used by the stage deployment."
  type        = string
  default     = "ap-south-1"
}

variable "project_name" {
  type    = string
  default = "dataops-factory"
}

variable "deployment_role_name" {
  description = "Pre-existing bootstrap role assumed by Terraform in each account."
  type        = string
  default     = "dataops-terraform-deployer"
}

variable "control_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.control_account_id))
    error_message = "control_account_id must contain 12 digits."
  }
}

variable "commerce_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.commerce_account_id))
    error_message = "commerce_account_id must contain 12 digits."
  }
}

variable "finance_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.finance_account_id))
    error_message = "finance_account_id must contain 12 digits."
  }
}

variable "customer_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.customer_account_id))
    error_message = "customer_account_id must contain 12 digits."
  }
}

variable "operations_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.operations_account_id))
    error_message = "operations_account_id must contain 12 digits."
  }
}

variable "lambda_artifact_dir" {
  description = "Directory populated by scripts/package_lambdas.py before terraform plan."
  type        = string
  default     = "../../dist/lambdas"
}

variable "emr_release_label" {
  description = "Explicitly reviewed EMR Serverless release label for the stage region."
  type        = string
  default     = "emr-7.5.0"
}
