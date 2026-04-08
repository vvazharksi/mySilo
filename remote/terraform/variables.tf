variable "bucket" {
  description = "Name of the existing S3 bucket mySilo syncs to."
  type        = string
}

variable "region" {
  description = "AWS region the bucket lives in."
  type        = string
  default     = "eu-west-1"
}

variable "function_name" {
  description = "Name for the Lambda function."
  type        = string
  default     = "mysilo-state-updater"
}

variable "remote_state_key" {
  description = "S3 key for the remote state file written by Lambda."
  type        = string
  default     = ".mysilo_remote_state.json"
}
