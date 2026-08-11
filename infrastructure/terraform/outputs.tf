output "workload_state_machine_arn" {
  value = module.control_plane.workload_state_machine_arn
}

output "run_ledger_table" {
  value = module.control_plane.run_ledger_table
}

output "register_run_function_name" {
  value = module.control_plane.register_run_function_name
}

output "mark_failed_function_name" {
  value = module.control_plane.mark_failed_function_name
}

output "manifest_buckets" {
  value = {
    commerce   = module.commerce_workload.manifest_bucket_name
    finance    = module.finance_workload.manifest_bucket_name
    customer   = module.customer_workload.manifest_bucket_name
    operations = module.operations_workload.manifest_bucket_name
  }
}

output "redshift_publication" {
  value = {
    workgroup = module.control_plane.redshift_workgroup_name
    database  = module.control_plane.redshift_database
  }
}

output "redshift_workgroup_name" {
  value = module.control_plane.redshift_workgroup_name
}

output "redshift_database" {
  value = module.control_plane.redshift_database
}

output "redshift_admin_secret_arn" {
  value = module.control_plane.redshift_admin_secret_arn
}
