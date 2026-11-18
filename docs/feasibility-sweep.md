> 由 `python -m evals.feasibility_sweep --write` 生成，请勿手工编辑。

# 可行性判定的影响面

加一条生成前检查很便宜，风险是它把**本来能执行**的 brief 也拒掉，而这个风险在促成它的那组用例里看不见——那些用例本来就是写来失败的。所以这里扫全部四组 brief，并把「盲测集必须全部可执行」当作硬标准：那 320 条是挖掘器从真实失败里筛出来的，每一条都先跑通过 good provider 才被收进来。

| 用例组 | 条数 | 判为不可行 | 不可行原因 |
| --- | ---: | ---: | --- |
| 主回归集 | 80 | 11 | `BUDGET_BELOW_MINIMUM` ×6、`TIME_BELOW_MINIMUM` ×2、`UNSUPPORTED_CITY` ×2、`CONFLICTING_CONSTRAINTS` ×2 |
| 三城补充集 | 12 | 3 | `UNKNOWN_REQUIRED_LOCATION` ×1、`TIME_BELOW_REQUIRED_LOCATIONS` ×1、`TIME_BELOW_MINIMUM` ×1 |
| 盲测集 | 320 | 0 | — |
| 红队集 | 30 | 6 | `TIME_BELOW_MINIMUM` ×1、`BUDGET_BELOW_MINIMUM` ×1、`UNSUPPORTED_CITY` ×1、`UNKNOWN_REQUIRED_LOCATION` ×1、`CONFLICTING_CONSTRAINTS` ×1、`ALL_LOCATIONS_EXCLUDED` ×1 |

盲测集 320 条**全部判为可执行**，新增规则没有误伤。

## 为什么只对盲测集断言

主回归集与红队集里都有**故意不可行**的用例，它们被判拒绝才是对的；脚本无法从数据里可靠区分「故意不可行」和「被新规则误伤」，所以不对这两组下断言，只列数字。盲测集不同：它的每一条都经过 good provider 实跑并被接受，全可执行是它的定义。

改动判据之后重跑这一页：

```bash
python -m evals.feasibility_sweep --write
```
