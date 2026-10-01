# 可选本地 E5 服务

这里包含本地字段语义向量服务的源码、准备模型工具、基准脚本和合成测试。主网申程序的基础安装不要求启动此服务。

单独建立 Python 环境并安装 `requirements.txt`；开发测试还使用 `requirements-dev.txt`。模型准备使用 `scripts/prepare_model.py`，先查看该脚本的帮助参数。模型权重、个人字段语义库、数据库和基准输出不随仓库发布。

配置通过 `E5_MODEL_DIR`、`E5_DATA_DIR`、`E5_MODEL_VERSION` 等环境变量提供，详见 `app/config.py`。启动入口是 `start_service.py`；导入模块不会自动启动服务，启动器不会自动下载模型或安装依赖。

从此目录运行 `python -m pytest tests` 可进行服务合成测试。测试使用虚构数据与假编码器；模型实际推理与基准需另行准备权重，不把合成检查称为真实性能测试。
