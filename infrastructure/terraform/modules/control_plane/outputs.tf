output "workload_state_machine_arn" {
  value = aws_sfn_state_machine.workload.arn
}

output "run_ledger_table" {
  value = aws_dynamodb_table.run_ledger.name
}

output "register_run_function_name" {
  value = aws_lambda_function.register_run.function_name
}

output "mark_failed_function_name" {
  value = aws_lambda_function.mark_failed.function_name
}

output "redshift_workgroup_name" {
  value = aws_redshiftserverless_workgroup.catalog.workgroup_name
}

output "redshift_database" {
  value = aws_redshiftserverless_namespace.catalog.db_name
}

output "redshift_admin_secret_arn" {
  value = aws_redshiftserverless_namespace.catalog.admin_password_secret_arn
}
