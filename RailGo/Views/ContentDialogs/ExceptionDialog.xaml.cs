using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System;
using System.Diagnostics;
using System.Threading.Tasks;
using Windows.ApplicationModel.DataTransfer;

namespace RailGo.Views.ContentDialogs;

public sealed partial class ExceptionDialog : ContentDialog
{
    private readonly Exception _exception;

    public ExceptionDialog(Exception exception)
    {
        this.InitializeComponent();
        _exception = exception;
        InitializeData();

        this.PrimaryButtonClick += OnPrimaryButtonClick;
        this.SecondaryButtonClick += OnSecondaryButtonClick;
        this.CloseButtonClick += OnCloseButtonClick;
    }

    private void InitializeData()
    {
        ExceptionTypeText.Text = $"异常类型: {_exception.GetType().Name}";
        ErrorMessageTextBox.Text = _exception.Message;
        StackTraceTextBox.Text = _exception.StackTrace ?? "无堆栈跟踪信息";
    }

    private void OnPrimaryButtonClick(ContentDialog sender, ContentDialogButtonClickEventArgs args)
    {
        // 直接关闭
    }

    private void OnSecondaryButtonClick(ContentDialog sender, ContentDialogButtonClickEventArgs args)
    {
        args.Cancel = true;
        _ = DispatcherQueue.TryEnqueue(async () =>
        {
            CopyErrorToClipboard();
            sender.SecondaryButtonText = "已复制";
        });
    }

    private void OnCloseButtonClick(ContentDialog sender, ContentDialogButtonClickEventArgs args)
    {
        args.Cancel = true;
        _ = DispatcherQueue.TryEnqueue(async () =>
        {
            this.Hide();
            RestartApplication();
        });
    }

    private void CopyErrorToClipboard()
    {
        var errorText = $"异常类型: {_exception.GetType().Name}\n\n" +
                       $"错误信息: {_exception.Message}\n\n" +
                       $"堆栈跟踪:\n{_exception.StackTrace}";

        var dataPackage = new DataPackage();
        dataPackage.SetText(errorText);
        Clipboard.SetContent(dataPackage);
    }

    private void RestartApplication()
    {
        Process.Start(Process.GetCurrentProcess().MainModule.FileName);
        Application.Current.Exit();
    }
}