# 记录格式与命令

运行环境：Python 3.10+，仅标准库。使用本机可用的 Python，不依赖作者电脑的目录。

## 本单 spec

JSON须含：
- job_id：本地订单识别名。
- masters：至少一个 {id, path}，path为本单基准文件，相对spec目录或绝对本地路径。
- panels：至少一个 {id, master_id, baseline_status, baseline_evidence, reading_direction, placement_rule}。baseline_status为confirmed或unknown；confirmed必须说明用户/订单的确认依据，脚本不验证该说法真假。方向/位置无依据就写unknown，不可猜成已确认。
- views：至少一个 {id, path, panels, master_ids}，明确该视角可见哪些面及对应母版。panels/master_ids必须引用前述ID；可见面要求的母版不能遗漏。
- protected：本次必须保持的内容字符串列表，可为空；为空时仍要检查没有额外改动。
- error_ids：本次从历史库选择的编号，可为空。编号只是案例参照，不是本单要求。脚本只抄选中的案例，不智能推断全部适用性。
- library_path：可选，本地错误记录JSON路径；不设置时不会读取任何默认私人库。

视角有限、图案有正常遮挡或本单不允许看完整时，按具体范围记unverified或说明不适用，不能从看不到推成正确。脚本要求每视角每个可见面都有六类记录，单视角仍检查与母版关系。

## 使用

prepare的out必须不存在；不覆盖spec、原图、已有报告。脚本拒绝网络路径/URL、目录冒充文件、重复ID、缺失面/母版引用。它按字节计算版本哈希，不渲染、不OCR、不读取图片含义。
prepare生成：
- plan.json：声明资料、来源哈希和检查范围。
- review.json：所有检查初始unverified，实际看图后填。
- checklist.md：便于人工逐项查看。
- history.json：仅所选历史条目及其来源哈希。选过的历史记录在库发生变化时需重新核实，不将旧记录误当最新。

每个view/panel的六项：orientation（阅读方向/镜像）、placement（锚点/中线/间距）、copy（可见文字）、integrity（完整/裁切/变形）、geometry（比例/透视/面对应）、protected（保护项及范围外）；另有cross_view_consistency（各角度和母版是否一致）。
状态为pass、fail、unverified或na。pass/fail需填写reviewer、reviewed_at（ISO日期时间带时区）、observation以及至少一个本地evidence {path, sha256}。na仅允许copy或protected项，并需具体说明为何不适用；方向、位置、完整性、几何及跨视图不能跳过。unverified可先写阻塞原因；不被视为通过。
所有证据路径相对review.json目录解析或为本地绝对路径。check会重新读取证据和master/view文件计算哈希；不得将客户文件改动后仍称同一次检查有效。若素材改变，重新prepare到新目录并重新检查受影响项。

check结果：
- 0：记录完整且填写为通过/合理不适用；这不是视觉认证。
- 2：存在fail/unverified、未知基准或待解决事项。
- 1：数据结构错误、缺失/重复检查、引用丢失或文件哈希变化。
不依据退出码替用户或工厂签署生产放行。报告保留实际观察者与限制。

错误库不可读取而error_ids为空时允许准备本单记录，但warnings明确标注没有读到历史库；若指定了历史编号则失败，不能假装套用了对应记录。