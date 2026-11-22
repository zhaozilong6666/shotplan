> 由 `python -m evals.batch_kill_lab --write` 生成，请勿手工编辑。

# batch 硬杀：返回即落库，杀进程也不丢

3 轮；每轮全新库，batch 模式发完请求后由 `server()` 退出时 `taskkill /F /T` 硬杀 uvicorn，再重新打开库数行。

## 结果

| 轮 | 返回 200 | 硬杀后行数 | 丢写 |
| ---: | ---: | ---: | ---: |
| 1 | 20 | 20 | 0 |
| 2 | 20 | 20 | 0 |
| 3 | 20 | 20 | 0 |

总丢写：**0**。

## 语义

- `batch` 与 `sync` 都等到 commit 才返回；硬杀发生在返回之后，所以不应丢行。
- `async` 不等待 commit，硬杀会丢队列里的行；这条差异已有测量（tests/test_write_behind.py）。
- 本测试只覆盖 Windows `taskkill /F`；POSIX 的 SIGTERM 可能被 uvicorn 优雅处理，不能拿来当硬杀证据。

## 边界

只验证「已返回的请求」；请求在途中被杀的语义不在本测试里。
