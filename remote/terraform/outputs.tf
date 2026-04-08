output "lambda_function_name" {
  description = "Name of the deployed Lambda function."
  value       = aws_lambda_function.mysilo.function_name
}

output "lambda_function_arn" {
  description = "ARN of the deployed Lambda function."
  value       = aws_lambda_function.mysilo.arn
}

output "remote_state_key" {
  description = "S3 key of the remote state file maintained by Lambda."
  value       = var.remote_state_key
}
