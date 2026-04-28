# 竞品商标样本图片

将竞品商标的样本图片放入此目录，用于 YOLO 模型训练的数据标注参考。

## 目录结构建议

```
trademarks/
├── brand_a/          # 品牌A的商标样本
│   ├── sample1.png
│   ├── sample2.png
│   └── ...
├── brand_b/          # 品牌B的商标样本
│   ├── sample1.png
│   └── ...
└── README.md
```

## 注意事项

- 图片应包含商标在不同背景、角度、尺寸下的样本
- 建议每个商标至少提供 50-100 张不同场景的样本
- 样本图片将用于 LabelImg/Roboflow 标注，生成 YOLO 格式训练数据
- 标注后的数据应放入 `models/dataset/` 目录
