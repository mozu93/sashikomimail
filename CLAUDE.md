# プロジェクト固有ルール（sashikomimail）

## PyQt6レイアウト：入力フォームの横幅

- 名前・メールアドレスなど1行の `QLineEdit` を並べるフォーム（`QGroupBox` +
  `QVBoxLayout`/`QFormLayout`）を親の `QHBoxLayout` に追加するときは、
  ストレッチ係数を付けて親いっぱいに広げない。
  - NG: `root.addWidget(editor_box, 1)`
  - OK: `editor_box.setFixedWidth(420)` としたうえで
        `root.addWidget(editor_box)` → `root.addStretch(1)` で
        右側の余白をストレッチに逃がす
  - `setMaximumWidth` だけでは不十分：フォーム内容の `sizeHint()` が
    420pxより小さいと、その幅のままに縮んでしまい上限まで広がらない
    （実例：`QLineEdit`2つだけの連絡先タブは`sizeHint`が420pxを自然に
    超えていたので気づかなかったが、`QPlainTextEdit`本文を含む
    テンプレート・署名タブでは278px程度にしかならず「連絡先タブより
    狭い」という見た目のズレが発生した）。`setFixedWidth` で
    最小・最大を両方420pxに固定し、内容に関わらず幅を保証すること。
- `TemplateTab`（テンプレート）・`SignatureTab`（署名）・`CCContactsTab`
  （連絡先）は、いずれも編集フォーム側の `QGroupBox` に
  `setFixedWidth(420)` を設定し、右側の余白は `root.addStretch(1)` で
  埋める構成に統一している。`QPlainTextEdit`（本文）を含む場合も例外なく
  420px幅に収め、この3タブで見た目を揃える。
- 新しいフォームの幅を420pxに合わせたら、`box.width()` が実際に420に
  なっているかを `findChild(QGroupBox)` 等で確認する（`sizeHint`だけを
  見て「`setMaximumWidth`したから大丈夫」と判断しない）。

## 対応最小解像度：1366×768

- このアプリの実利用環境は13.3インチノートPC（1366×768）を含む。
  `ComposeTab`（作成・送信タブ）の左右2ペイン（`QSplitter`）は、
  ウィンドウ幅1366px前後でも横スクロールが出ないことを実機解像度で
  確認すること。
- ボタンや入力欄を「3. 宛先設定」「5. 共通添付」など既存のグループボックスに
  追加・変更したときは、そのグループボックス単体の `minimumSizeHint()` が
  想定外に増えていないか確認する（1行の `QHBoxLayout` に長いラベルの
  `QPushButton` を足すと、フォーム全体の必要最小幅を大きく押し上げる）。
- `QSplitter.setSizes([...])` の左右比率と `MainWindow.setMinimumSize(...)`
  は、両ペインの合計必要最小幅（左パネルと右パネルそれぞれの中で最も幅を
  要求するグループボックスの `minimumSizeHint().width()` の合計）を
  下回らないように保つ。値を変えたら 1366×728 相当のウィンドウサイズで
  実際にレンダリングして確認する（`ComposeTab` を単体で
  `resize(1366, 728)` して `grab()` で画像保存すると手早く検証できる）。

### 縦方向のはみ出し（横スクロールと同じくらい起きる）

- グループボックスやフォーム行をタブへ追加したら、**幅だけでなく高さも**
  確認する。1366×768のウィンドウでタブに使える高さは約714px。
  タブの `minimumSizeHint().height()` がこれを超えると、`QVBoxLayout` は
  最小高さ以下へ押し潰され、**グループ内の各行が重なって表示される**
  （実例：v1.3.0で`SettingsTab`にGmailの設定欄4行を足したところ、
  「Microsoft 365」グループの7行が重なり、ラベルもボタンも判読不能になった）。
- 縦に伸び続けるタブ（設定など、項目が増えていく画面）は最初から
  `QScrollArea`（`setWidgetResizable(True)` + `setFrameShape(NoFrame)`）へ
  入れる。`ComposeTab`の右ペインと`SettingsTab`はこの構成。
- 確認は全タブまとめて機械的にできる。`MainWindow` を `resize(1366, 768)` し、
  `w.tabs.widget(0).height()`（タブに使える高さ）と各タブの
  `minimumSizeHint().height()` を比べる。前者を超えるタブがあれば
  そのタブは崩れている。
