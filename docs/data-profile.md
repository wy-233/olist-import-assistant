# 数据盘点报告（第 2 天）

> 本报告由 `scripts/profile_data.py` 只读扫描 `data/raw/` 生成，所有数字来自代码统计。
> 复现命令（仓库根目录）：`.venv/Scripts/python.exe -I scripts/profile_data.py`
> 本报告只陈述事实，业务判定（异常还是提醒）见 `docs/decisions.md`，由人决定。

## 1. 文件核对

| 文件 | 字节数 | 数据行数 | SHA256 与 SOURCE.md |
|---|---|---|---|
| olist_products_dataset.csv | 2379446 | 32951 | 一致 |
| product_category_name_translation.csv | 2613 | 71 | 一致 |

## 2. 商品表

- 9 列，所有行列数一致，无首尾空白。
- `product_id`：无空值、无重复，32951 行长度均为 32；无整行重复。
- 所有数值列无非数字、无小数。

| 列 | 空值数 | 最小 | 最大 | 为 0 |
|---|---|---|---|---|
| product_category_name | 610 | — | — | — |
| product_name_lenght | 610 | 5 | 76 | 0 |
| product_description_lenght | 610 | 4 | 3992 | 0 |
| product_photos_qty | 610 | 1 | 20 | 0 |
| product_weight_g | 2 | 0 | 40425 | 4 |
| product_length_cm | 2 | 7 | 105 | 0 |
| product_height_cm | 2 | 2 | 105 | 0 |
| product_width_cm | 2 | 6 | 118 | 0 |

### 空值的行间关系

- 类目、名称长度、描述长度、图片数这 4 列的 610 个空值落在**同一批行**（脚本逐行比对集合相等）。
- 重量与三个尺寸的 2 个空值也落在同一批行：
  - `09ff539a621711667c43eba6a3bd8466`：类目 `bebes`，重量尺寸全空。
  - `5eb564652db742ff8f28759cd8d2652a`：类目也为空，重量尺寸全空。

### 重量为 0 的 4 行

`81781c0fed9fe1ad6e8c81fca1e1cb08`、`8038040ee2a71048d4bdbbdc985b69ab`、`36ba42dd187055e1fbe943b2d11430ca`、`e673e90efa65a5409ff4196c038bb5af`。
4 行类目均为 `cama_mesa_banho`，尺寸均为 30 × 25 × 30。

## 3. 类目翻译表

- 71 行，类目主键无重复，无空值。
- 商品表非空类目 73 个，翻译表无多余类目。
- 商品表有而翻译表无的类目：

| 类目 | 涉及商品数 |
|---|---|
| pc_gamer | 3 |
| portateis_cozinha_e_preparadores_de_alimentos | 10 |

## 4. 未验证事项

- 本报告不判断数值是否“合理”（如重量 40425 g、尺寸 118 cm 是否异常），阈值需另行决策。
- 未检查类目名的大小写或拼写变体，仅做了精确匹配。
