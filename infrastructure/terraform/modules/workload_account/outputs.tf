output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}

output "manifest_bucket_arn" {
  value = aws_s3_bucket.manifests.arn
}

output "manifest_bucket_name" {
  value = aws_s3_bucket.manifests.id
}

output "signer_key_arn" {
  value = aws_kms_key.signer.arn
}

output "data_key_arn" {
  value = aws_kms_key.data.arn
}

output "lambda_function_arn" {
  value = aws_lambda_function.workload.arn
}

output "glue_job_name" {
  value = aws_glue_job.standard.name
}

output "emr_application_id" {
  value = aws_emrserverless_application.heavy.id
}

output "runtime_role_arn" {
  value = aws_iam_role.runtime.arn
}

output "artifact_bucket_name" {
  value = aws_s3_bucket.artifacts.id
}
