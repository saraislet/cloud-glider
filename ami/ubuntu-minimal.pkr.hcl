packer {
  required_version = ">= 1.11.0, < 2.0.0"
  required_plugins {
    amazon = {
      version = "= 1.8.2"
      # Local patched plugin; install with scripts/install_packer_amazon.py.
      source = "github.com/cloud-glider/amazon"
    }
  }
}

variable "source_ami" { type = string }
variable "subnet_id" { type = string }
variable "security_group_id" { type = string }
variable "builder_instance_profile" { type = string }
variable "daemon_artifact" { type = string }
variable "daemon_sha256" {
  type = string
  validation {
    condition     = can(regex("^[a-f0-9]{64}$", var.daemon_sha256))
    error_message = "Provide the exact lowercase SHA-256 of the daemon artifact."
  }
}
variable "source_commit" {
  type = string
  validation {
    condition     = can(regex("^[a-f0-9]{40}$", var.source_commit))
    error_message = "Use a full committed Git revision."
  }
}
variable "ssm_version" {
  type = string
  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$", var.ssm_version))
    error_message = "Pin an SSM Agent release."
  }
}
variable "ssm_sha256" {
  type = string
  validation {
    condition     = can(regex("^[a-f0-9]{64}$", var.ssm_sha256))
    error_message = "Provide the verified ARM64 SSM deb SHA-256."
  }
}

source "amazon-ebssurrogate" "ubuntu" {
  region = "us-west-2"
  # Fail if the selected ID is not an official Ubuntu Minimal 24.04 ARM64 AMI.
  source_ami_filter {
    filters = {
      image-id            = var.source_ami
      name                = "ubuntu-minimal/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-minimal-*"
      architecture        = "arm64"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    owners = ["099720109477"]
  }
  instance_type               = "t4g.micro"
  subnet_id                   = var.subnet_id
  security_group_id           = var.security_group_id
  associate_public_ip_address = true
  iam_instance_profile        = var.builder_instance_profile
  ssh_username                = "ubuntu"
  ssh_interface               = "session_manager"
  ssh_timeout                 = "15m"
  user_data = templatefile("ssm-user-data.sh.pkrtpl", {
    version = var.ssm_version, sha256 = var.ssm_sha256
  })
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  launch_block_device_mappings {
    device_name           = "/dev/sdf"
    volume_size           = 2
    volume_type           = "gp3"
    iops                  = 3000
    encrypted             = true
    delete_on_termination = true
  }
  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 8
    volume_type           = "gp3"
    iops                  = 3000
    delete_on_termination = true
    omit_from_artifact    = true
  }
  ami_root_device {
    source_device_name    = "/dev/sdf"
    device_name           = "/dev/sda1"
    volume_size           = 2
    volume_type           = "gp3"
    delete_on_termination = true
  }
  ami_name                = "cloud-glider-ubuntu-24.04-arm64-${substr(var.source_commit, 0, 12)}-{{timestamp}}"
  ami_description         = "Private Cloud Glider candidate; 2 GiB root; requires release approval"
  ami_architecture        = "arm64"
  ami_virtualization_type = "hvm"
  boot_mode               = "uefi"
  ena_support             = true
  imds_support            = "v2.0"
  # Never publish publicly, copy to other Regions, or overwrite old images.
  ami_users             = []
  ami_groups            = []
  ami_regions           = []
  force_deregister      = false
  force_delete_snapshot = false
  run_tags = {
    project = "cloud-glider"
    purpose = "image-build"
    Name    = "cloud-glider-image-builder"
  }
  run_volume_tags = { project = "cloud-glider", purpose = "image-build" }
  snapshot_tags   = { project = "cloud-glider", purpose = "daemon-image" }
  tags = {
    project       = "cloud-glider"
    purpose       = "daemon-image"
    approval      = "candidate"
    source-commit = var.source_commit
    source-ami    = var.source_ami
    daemon-sha256  = var.daemon_sha256
  }
}

build {
  sources = ["source.amazon-ebssurrogate.ubuntu"]
  provisioner "shell" { inline = ["mkdir -p /tmp/glider-image"] }
  provisioner "file" {
    source      = "${path.root}/files/"
    destination = "/tmp/glider-image/"
  }
  provisioner "file" {
    source      = var.daemon_artifact
    destination = "/tmp/glider-image/daemon.tar.gz"
  }
  provisioner "shell" {
    environment_vars = [
      "DAEMON_SHA256=${var.daemon_sha256}", "SOURCE_COMMIT=${var.source_commit}"
    ]
    execute_command = "chmod +x {{ .Path }}; sudo env {{ .Vars }} bash {{ .Path }}"
    script          = "${path.root}/files/install.sh"
  }
  provisioner "shell" {
    inline = ["sudo bash /tmp/glider-image/small-root.sh"]
  }
  post-processor "manifest" {
    output     = ".artifacts/ami/ami-build-manifest.json"
    strip_path = true
    custom_data = {
      daemon_sha256  = var.daemon_sha256
      source_commit = var.source_commit
      source_ami    = var.source_ami
      root_gib      = "2"
      approval      = "candidate"
    }
  }
}
