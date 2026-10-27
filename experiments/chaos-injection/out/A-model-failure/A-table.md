# 剧本 A 观测表

| mode | exit | wall(s) | HTTP 尝试 | 重试间隔(s) | final 长度 | blocked | turns | pre_step | hook_error | 宪法在 prompt |
|---|---:|---:|---:|---|---:|---|---:|---:|---:|---|
| `500` | 1 | 17.99 | 7 | 0.0, 0.55, 0.96, 1.92, 4.25, 7.51 | 0 | False | 1 | 1 | 0 | True |
| `empty` | 0 | 3.27 | 2 | 0.0 | 0 | False | 1 | 1 | 0 | True |
| `fabricated` | 0 | 2.79 | 2 | 0.0 | 23 | False | 1 | 1 | 0 | True |
| `hang` | None | 150.03 | 2 | 0.0 | 0 | False | 1 | 1 | 0 | True |
| `ok` | 0 | 2.84 | 2 | 0.0 | 7 | False | 1 | 1 | 0 | True |

## 逐用例 stderr（截断）

- `500`: `dsh: SERVER: mock upstream 500`
- `empty`: `(空)`
- `fabricated`: `(空)`
- `hang`: `(空)`
- `ok`: `(空)`
