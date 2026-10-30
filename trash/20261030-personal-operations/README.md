# 2026-10-30 私有部署历史归档

本目录保存原私有部署的历史材料，**不是当前安装、发布、排障或运行入口**。文件保持原始字节，未通过删除安全校验把专用脚本改成任意目标操作器；不要直接执行、导入到运行服务，或将历史常量视为当前配置。归档不代表凭据轮换、权限授予或任何生产操作已经完成。

## 归档范围

| 原位置 | 归档位置 | 原因 |
|---|---|---|
| `tools/release_access.py` | [tools/release_access.py](tools/release_access.py) | 特定 AstrBot 版本的管理密钥创建／撤销，包含固定来源散列与历史发布身份；会修改真实数据库，不是通用只读探针 |
| `tools/release_apply.py` | [tools/release_apply.py](tools/release_apply.py) | 绑定原主机插件目录、解释器、仪表盘端口、systemd 单元与容器名的受控部署器 |
| `tools/sid_native_disable.py` | [tools/sid_native_disable.py](tools/sid_native_disable.py) | 原命令 API 不支持所需权限时的一次性数据库配置回退，依赖两个发布助手 |
| `tools/runtime_routing_config.py` | [tools/runtime_routing_config.py](tools/runtime_routing_config.py) | 原部署特定路由设置变更，依赖发布权限助手，固定仪表盘与原源码根目录 |
| `plugin/prompts.py` | [plugin/prompts.py](plugin/prompts.py) | 已无运行引用的旧提示词实现；保留原始历史，不作为当前人格配置 |
| `plugin/prompts_v2.py` | [plugin/prompts_v2.py](plugin/prompts_v2.py) | 已无运行引用的旧提示词／私人 Q-API 资料；不应随活跃机器人运行加载 |

四个管理助手整体归档以保留内部导入关系；对应测试仅从归档路径加载标准库模块，模拟数据库／网络边界，不执行真实管理操作。机器人模型探针、消息与工具契约检查仍在活跃工具目录，因为它们验证机器人本身，并非无关的 Q-API 计费实现。

更早的 Q-API 事实合并与人格迁移脚本已位于 [2026-10-27 归档](../20261027-repository-cleanup/tools/ops/)，本次不重复移动或改写旧记录。

## 恢复与验证

四个助手在迁移前已由 git 记录。需要研究历史实现时，读取归档或比较原提交；若确需恢复，先评审目标、依赖与权限，再通过新的仓库变更恢复，不能把本目录当作受支持的运维接口。原检查、备份、窄权限、精确插件重载及失败不降级为重启的边界全部原样保留。
