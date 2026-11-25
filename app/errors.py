from flask import jsonify


MESSAGES = {
    "EMPTY_FILE": "文件为空",
    "BLANK_FILE": "文件只有空白",
    "HEADER_ONLY": "文件只有表头",
    "HEADER_MISMATCH": "表头与本项目字段不一致",
    "UNCLOSED_QUOTE": "引号未闭合，文件已拒收",
    "TOO_MANY_ROWS": "数据行超过上限",
    "FILE_TOO_LARGE": "文件超过大小上限",
    "KEY_MISSING": "主键为空",
    "CONFLICT_DUPLICATE": "同一键存在冲突内容",
    "SAMPLE_UNKNOWN": "样例不存在",
    "STAGE_UNKNOWN": "阶段不存在",
    "RUN_NOT_FOUND": "批次不存在",
    "STALE_REVIEW": "批次已被更新，请刷新后复核",
    "GATE_BLOCKED": "存在阻断项，不能签发",
    "NOT_APPROVED": "批次尚未签发",
    "BAD_REQUEST": "请求内容不完整",
    "TICKET_NOT_FOUND": "原票余额账户不存在",
    "ORDER_NOT_FOUND": "改期单不存在",
    "TICKET_ID_REQUIRED": "缺少原票标识",
    "ORDERS_REQUIRED": "缺少改期单",
    "INVALID_BALANCE": "原票余额不合法",
    "ORDER_CONFLICT": "相同改期单提交内容不一致",
    "BALANCE_CONFLICT": "余额变更与已有抵扣冲突",
    "BALANCE_BUSY": "余额正在处理，请重试",
    "ORDER_NOT_CONFIRMABLE": "当前改期单不能确认抵扣",
    "CONFIRMED_DEDUCTION_LOCKED": "已确认抵扣不能撤销",
    "ORDER_CANCELED": "改期单已撤销",
}


class DomainIssue(Exception):
    def __init__(self, code: str, status: int = 400, detail: str | None = None):
        self.code = code
        self.status = status
        self.detail = detail
        super().__init__(code)


def error_response(exc: DomainIssue):
    return jsonify(error=exc.detail or MESSAGES.get(exc.code, "请求无法处理"), code=exc.code), exc.status
