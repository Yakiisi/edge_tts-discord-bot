# Discord 読み上げ Bot

このプロジェクトは、Discordサーバーでの会話をリアルタイムで読み上げるBotです。  
Edge TTSを使用して自然な日本語音声を生成し、GUIによる操作や辞書編集も可能です。

## 機能一覧

- Edge TTSによる日本語読み上げ（Nanami / Keita）
- ユーザーごとの読み上げ速度・音声のカスタマイズ
- サーバー辞書 / グローバル辞書機能
- スラッシュコマンド・プレフィックスコマンド両対応
- 自動VC退出機能
- GUI（Tkinter）でBotの状態・辞書を管理可能

## 必要環境

- Python 3.9 以上
- Discord Bot Token
- 必須ライブラリ（`requirements.txt` で管理）：

```txt
discord.py
python-dotenv
edge-tts
psutil
````

## .envファイルの例

 `.env` を作成し、以下のようにTOKENを設定してください。

```env
BOT_TOKEN=your_discord_bot_token_here
```

## 実行方法

```bash
python main.py
```

TkinterによるGUIが起動し、同時にBotも起動します。

## 主なコマンド

| コマンド                        | 概要                                |
| --------------------------- | --------------------------------- |
| `/join`                     | VCに接続します                          |
| `/leave`                    | VCから切断します                         |
| `/set_reading_channel`      | 読み上げ対象のテキストチャンネルを設定します            |
| `/setvoice`                 | 読み上げに使用する音声を変更します（Nanami / Keita） |
| `/setspeed`                 | 読み上げ速度を設定します（-50% ～ +200%）        |
| `/add_word`, `/remove_word` | サーバー辞書への単語追加・削除                   |
| `/show_dict`                | 辞書の内容を表示します                       |
| `/status`                   | Botの動作状況を確認します                    |
| `/invite`                   | 招待リンクを表示します                       |

## GUIについて

* ダッシュボード：Botの接続状況や読み上げチャンネルの管理
* グローバル辞書：GUIから登録・編集・削除可能
* ログ：Botの処理状況やエラーをリアルタイムで確認

## 注意点

* `.env` にトークンが正しく設定されていない場合、Botは起動しません。
* Edge TTSはインターネット接続を必要とします。

## 問い合わせ

* 作成者: 石君
* X（旧Twitter）: [@yakiisi2gou](https://x.com/yakiisi2gou)
