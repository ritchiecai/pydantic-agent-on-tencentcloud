############################################################
# 输出
# 文件名单数 output.tf，与 landing-zone-booster 各模块命名一致。
############################################################

output "service_url" {
  description = "CLB 公网入口 URL（有 HTTPS 证书用 https，否则 http）"
  # clb-instance 模块输出 clb_vips（复数 list），取第一个 VIP
  value = var.ssl_certificate_id == "" ? "http://${module.clb.clb_vips[0]}" : "https://${module.clb.clb_vips[0]}"
}

output "cvm_private_ip" {
  description = "CVM 私网 IP"
  value       = module.cvm.private_ip
}

output "clb_vip" {
  description = "CLB VIP"
  value       = module.clb.clb_vips[0]
}
