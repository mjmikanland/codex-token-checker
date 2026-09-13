# Codex Token Checker

Windows上のCodexローカルJSONLセッションログを読み取り専用で集計するGUIツールです。APIトークンを外部へ送信して検証するものではなく、`%USERPROFILE%\.codex\sessions` に保存されたログから使用量とRate Limit情報を表示します。

## 主な機能

- 今日、過去7日、過去30日、全期間のトークン集計
- 直近1時間・5時間の使用量
- Input / Cached Input / Output / Reasoning / Totalの表示
- プロジェクト別・セッション別の集計
- 過去7日間の日別グラフ
- ログ内のRate Limit使用率とプラン情報の表示
- 自動更新、タスクトレイ表示、Windowsログイン時の自動起動
- Rate Limit低下・ログ更新停止の目安通知

> Rate Limit表示はログ内の`used_percent`に基づく推定値です。契約上の残りトークンや実際のキャッシュ期限を直接取得するものではありません。

## 使い方（EXE）

1. GitHubのActionsまたはReleasesから`CodexTokenChecker.exe`を取得します。
2. WindowsでEXEを起動します。
3. Codexのログが存在すれば自動的に集計結果が表示されます。
4. 設定タブから自動更新やタスクトレイを変更できます。

既定のログフォルダは次のとおりです。

```text
C:\Users\<ユーザー名>\.codex\sessions
```

設定は次に保存されます。

```text
%APPDATA%\CodexTokenChecker\settings.json
```

## ソースから実行

Python 3.11以上とTkinterが必要です。WindowsではPythonインストーラーの`tcl/tk`を有効にしてください。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python codex_gui_v6.py
```

## テスト

```powershell
python -m unittest discover -s tests -v
```

テストはログ解析・使用量差分・Rate Limit抽出などの純粋ロジックを中心に実行します。実行環境にTkinterが必要です。

## Windows用EXEのビルド

PowerShellで次を実行します。

```powershell
.\build_codex_v1.ps1
```

または、依存関係をインストール済みなら次のように実行できます。

```powershell
python make_codex_icon.py
python -m PyInstaller --noconfirm --clean CodexTokenChecker.spec
```

生成物は`dist\CodexTokenChecker.exe`です。

## GitHub Actions

- `test.yml`: PushとPull Request時にUbuntu上で構文検証とユニットテストを実行します。
- `build-windows.yml`: `v*`タグ、手動実行、または正式リリース作成時にWindows用EXEをビルドします。成果物はActionsのArtifactとして保存され、リリース作成時はEXEが添付されます。

## 安全性と制限

アプリはCodexログを読み取り専用で扱い、ログファイルへ変更を書き込みません。ログ形式が変更された場合、集計対象のイベントを認識できなくなる可能性があります。現在のGUIはWindows利用を主対象としています。

## License

ライセンスは未指定です。配布範囲を決める場合は、別途LICENSEファイルを追加してください。

## Manus Usage Checker

`manus_usage_checker.py`は、Manusから保存した利用履歴のCSVまたはJSONを読み込み、ローカルで集計するWindows GUIです。Manusへログインしたり、認証情報・履歴データを外部へ送信したりしません。

### 起動方法

```powershell
python manus_usage_checker.py
```

画面の「履歴を開く」からCSVまたはJSONを選択してください。複数ファイルを同時に選択できます。

### 対応する列名

エクスポート形式の列名が多少異なっていても、次の代表的な列名を自動認識します。

| 内容 | 認識する例 |
|---|---|
| 日時 | `timestamp`, `created_at`, `date`, `datetime` |
| タスク | `task`, `task_name`, `name`, `title`, `prompt` |
| 状態 | `status`, `state`, `result`, `outcome` |
| 使用量 | `credits`, `usage`, `amount`, `cost`, `tokens`, `credits_used` |
| 実行時間 | `duration_seconds`, `duration`, `elapsed_seconds` |

JSONは配列形式、または`records`・`data`・`items`・`history`配下の配列形式に対応しています。実際のエクスポート列名が上記以外の場合は、対応する別名を追加できます。

### 表示・出力

- 利用件数、合計使用量、成功率、合計実行時間
- 過去14日の日別使用量グラフ
- タスク別集計
- 履歴一覧
- CSV / JSONへの分析結果出力

Manus版EXEは次のコマンドでビルドできます。

```powershell
.\build_manus_v1.ps1
```

生成物は`dist\ManusUsageChecker.exe`です。GitHub Actionsの`Build Windows EXE`を実行すると、Codex版とManus版の両方が`Windows-tools` Artifactとして生成されます。
