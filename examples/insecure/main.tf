# Intentionally insecure Terraform for demonstrating TerraSentinel.
# DO NOT deploy this. Every block below trips well-known checkov rules.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

# 1. S3 bucket with no encryption, no versioning, no logging, no public-access block.
resource "aws_s3_bucket" "data" {
  bucket = "my-company-customer-data"
}

# 2. Bucket ACL set to public-read — exposes objects to the world.
resource "aws_s3_bucket_acl" "data" {
  bucket = aws_s3_bucket.data.id
  acl    = "public-read"
}

# 3. Security group that opens SSH (22) to the entire internet.
resource "aws_security_group" "web" {
  name        = "web-sg"
  description = "Allow SSH and HTTP"

  ingress {
    description = "SSH from anywhere"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# 4. Publicly accessible, unencrypted RDS database with a hardcoded password.
resource "aws_db_instance" "main" {
  identifier          = "app-db"
  engine              = "postgres"
  instance_class      = "db.m5.4xlarge" # oversized: real cost impact
  allocated_storage   = 500
  username            = "admin"
  password            = "SuperSecret123!" # hardcoded secret
  publicly_accessible = true
  storage_encrypted   = false
  skip_final_snapshot = true
}

# 5. IAM policy granting full admin (*:*) to everything.
resource "aws_iam_policy" "admin" {
  name = "allow-everything"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "*"
        Resource = "*"
      }
    ]
  })
}
