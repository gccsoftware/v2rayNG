# v2rayNG 定制构建发布器

本仓库**不包含任何 v2rayNG 源码**，只保存一套 GitHub Actions 自动化：

1. 运行时克隆上游 [2dust/v2rayNG](https://github.com/2dust/v2rayNG) 的 `master` 分支源码（含子模块）；
2. 在构建机上对源码临时副本应用**包名修改**与**图标替换**（不改动上游）；
3. 编译各 ABI 的签名 APK；
4. 发布到本仓库的 GitHub Releases，**每个 Release 附带所有构建出的 v2rayNG APK**。

## 仓库结构

| 路径 | 说明 |
| --- | --- |
| `.github/workflows/build-release.yml` | 构建 & 发布工作流（定时检查 + 手动触发） |
| `scripts/customize.py` | 定制脚本：改写 `applicationId`、用自定义图标生成全套启动器图标 |
| `assets/icon.png` | 自定义应用图标（正方形 PNG，可随意替换） |
| `state/last_built_upstream_tag` | 定时任务构建状态记录（由 workflow 自动提交，勿手改） |

## 每天自动跟踪上游正式 Release

workflow 每天 **UTC 00:10**（北京时间 08:10）自动：

1. 查询 [2dust/v2rayNG](https://github.com/2dust/v2rayNG)（地址已写死）**最新一条非草稿的
   versioned Release**——注意上游 CI 会把 release 打上 prerelease 标记，因此不按
   prerelease 标记过滤，以发布时间最新者为准；
2. 与 `state/last_built_upstream_tag` 中记录的上次构建版本比对，相同则跳过；
3. 若有新版本：拉取**该 Release 对应 tag 的源码** → 改包名 / 改图标 → 构建 →
   以 `v<上游版本>-custom` 为 tag 发布**正式 Release** → 回写 state 文件。

首次运行时没有 state 记录，会立即构建上游当前最新版本一次。

## 手动构建

进入 **Actions → v2rayNG Build & Release → Run workflow**，参数：

| 输入 | 说明 | 默认 |
| --- | --- | --- |
| `package_name` | 自定义包名（`applicationId`，如 `com.mycorp.vpn`）。留空则使用仓库变量 `CUSTOM_PACKAGE_NAME`，再留空用 `com.renamed.v2rayng` | 空 |
| `upstream_ref` | 上游 2dust/v2rayNG 的分支 / tag | `master` |
| `release_tag` | 发布使用的 Release tag，留空自动生成 `v<版本>-custom-<时间戳>` | 空 |
| `prerelease` | 是否标记为 Pre-release | `true` |

每次运行都会创建（或更新）一个 Release，并上传 `fdroid` 与 `playstore` 两个 flavor、
每种 ABI（`arm64-v8a / armeabi-v7a / x86 / x86_64 / universal`）的签名 APK。
构建完成后 workflow 会自动执行**免安装校验**（见下文）。

### 免安装检查包名是否已改

构建结束后，workflow 会用 Android SDK 自带的 `aapt` 逐个解包读取每个 APK 的
`applicationId` 并与期望值（如 `com.mycorp.vpn` / `com.mycorp.vpn.fdroid`）比对，
同时打印签名 SHA-256 指纹校验不通过即构建失败。日志见
**Verify APK package name & signature** 步骤。

在本地任何装了 Android SDK build-tools 的机器上，也可以不安装直接查看：

```bash
# 输出第一行即包含 package: name='com.mycorp.vpn'
$ANDROID_HOME/build-tools/*/aapt dump badging xxx.apk | head -n1

# 查看签名证书指纹
$ANDROID_HOME/build-tools/*/apksigner verify --print-certs xxx.apk
```

### 包名通过 Actions 变量传入

在 **Settings → Secrets and variables → Actions → Variables** 中新建
`CUSTOM_PACKAGE_NAME = com.mycorp.vpn`，之后每次构建默认使用该包名；
Run workflow 时的手动输入优先级更高。

包名必须是合法的小写 Java 形式（至少两段，如 `com.example.vpn`），否则会直接构建失败。
fdroid flavor 会自动在新包名后追加 `.fdroid` 后缀。

### 替换图标

把新的正方形 PNG 覆盖到 `assets/icon.png` 并提交即可。脚本会自动生成：

* 旧版图标 `ic_launcher.png`（mdpi → xxxhdpi，48–192px）
* 旧版圆图标 `ic_launcher_round.png`（圆形蒙版裁切）
* 自适应前景 `ic_launcher_foreground.png`（108dp 画布，主体位于安全区内）
* 自适应背景色 `ic_launcher_background.xml`（自动取样图标四角颜色）

## 签名（可选但推荐）

不配置任何 secret 时，工作流会自动生成一个临时 keystore 并缓存在
Actions cache 中以尽量保持多次构建签名一致；但缓存可能失效，导致后续版本
无法覆盖安装。

长期发布请生成自己的 keystore 并配置以下 Secrets（与上游官方同名）：

```bash
keytool -genkeypair -v -keystore release.jks -alias mykey \
  -keyalg RSA -keysize 2048 -validity 10000
base64 -w 0 release.jks   # 输出放入 APP_KEYSTORE_BASE64
```

| Secret | 说明 |
| --- | --- |
| `APP_KEYSTORE_BASE64` | keystore 文件的 base64 |
| `APP_KEYSTORE_PASSWORD` | store 密码 |
| `APP_KEYSTORE_ALIAS` | key alias |
| `APP_KEY_PASSWORD` | key 密码 |

## 实现细节

* 只修改 `applicationId`，Kotlin `namespace`（`com.v2ray.ang`）保持不变，因此
  `BuildConfig`、JNI（`PKGNAME=com/v2ray/ang/service`）以及 Manifest 中的
  `${applicationId}` 占位符（FileProvider、widget 广播等）都会自动跟随新包名；
  同时补丁 `Utils.isXray()` 的包名前缀判断，保证与官方构建行为一致。
* 工具链（cmdline-tools、SDK platforms、NDK 版本）会从上游官方 workflow
  动态解析并带兜底默认值，NDK 版本由 `customize.py` 注入 `ndkVersion`。
* `libv2ray.aar` 按 `AndroidLibXrayLite` 子模块指向的 release tag 下载；
  `libhevtun` 用 NDK ndk-build 编译并按 commit/脚本哈希缓存。
* 构建参考了上游 `.github/workflows/build.yml` 的流程。
