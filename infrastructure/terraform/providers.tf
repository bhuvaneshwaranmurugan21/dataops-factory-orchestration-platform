provider "aws" {
  alias  = "control"
  region = var.aws_region
  assume_role {
    role_arn = "arn:aws:iam::${var.control_account_id}:role/${var.deployment_role_name}"
  }
  default_tags {
    tags = { Project = var.project_name, ManagedBy = "terraform", Environment = "stage" }
  }
}

provider "aws" {
  alias  = "commerce"
  region = var.aws_region
  assume_role {
    role_arn = "arn:aws:iam::${var.commerce_account_id}:role/${var.deployment_role_name}"
  }
  default_tags {
    tags = { Project = var.project_name, ManagedBy = "terraform", Environment = "stage" }
  }
}

provider "aws" {
  alias  = "finance"
  region = var.aws_region
  assume_role {
    role_arn = "arn:aws:iam::${var.finance_account_id}:role/${var.deployment_role_name}"
  }
  default_tags {
    tags = { Project = var.project_name, ManagedBy = "terraform", Environment = "stage" }
  }
}

provider "aws" {
  alias  = "customer"
  region = var.aws_region
  assume_role {
    role_arn = "arn:aws:iam::${var.customer_account_id}:role/${var.deployment_role_name}"
  }
  default_tags {
    tags = { Project = var.project_name, ManagedBy = "terraform", Environment = "stage" }
  }
}

provider "aws" {
  alias  = "operations"
  region = var.aws_region
  assume_role {
    role_arn = "arn:aws:iam::${var.operations_account_id}:role/${var.deployment_role_name}"
  }
  default_tags {
    tags = { Project = var.project_name, ManagedBy = "terraform", Environment = "stage" }
  }
}

