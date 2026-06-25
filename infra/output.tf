############################################################
# 输出
# 文件名单数 output.tf，与 landing-zone-booster 各模块命名一致。
############################################################

output "service_url" {
  description = "CLB 公网入口 URL（有 HTTPS 证书用 https，否则 http）"
  # clb-instance 模块输出 clb_vips（复数 list）。用 try() 兜底空 list：
  # - 销毁阶段（VIP 已被解绑/state 清零）不再因索引越界硬崩
  # - 边界场景（CLB 创建期尚未分配 VIP）也不会让 plan 失败
  value = (
    var.ssl_certificate_id == ""
    ? "http://${try(module.clb.clb_vips[0], "")}"
    : "https://${try(module.clb.clb_vips[0], "")}"
  )
}

output "cvm_private_ip" {
  description = "CVM 私网 IP"
  value       = module.cvm.private_ip
}

output "clb_vip" {
  description = "CLB VIP（CLB 尚未分配 VIP / 销毁阶段时为空字符串）"
  value       = try(module.clb.clb_vips[0], "")
}
