using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices.WindowsRuntime;
using Windows.Foundation;
using Windows.Foundation.Collections;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Data;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Navigation;
using System.Diagnostics;
using RailGo.Core.Query.Online;
using RailGo.ViewModels.Pages.Settings.DataSources;
using RailGo.ViewModels.Windows;
using RailGo.Core.Models.QueryDatas;

// To learn more about WinUI, the WinUI project structure,
// and more about our project templates, see: http://aka.ms/winui-project-info.

namespace RailGo.Views.Windows;

public sealed partial class GetOfflineDatabaseWindow : WindowEx
{
    public GetOfflineDatabaseWindowViewModel ViewModel
    {
        get;
    }

    public GetOfflineDatabaseWindow()
    {
        ViewModel = App.GetService<GetOfflineDatabaseWindowViewModel>();
        this.InitializeComponent();
        this.Width = 400;
        this.Height = 200;
        DisableTitleBarButtons();
        this.SetTitleBar(WindowTitleBar);
        this.ExtendsContentIntoTitleBar = true;
        this.Closed += OnWindowClosed;
        this.ViewModel.RequestCloseWindow += () => this.Close();

        ViewModel.WindowCloseConfirm = false;
    }
    private void DisableTitleBarButtons()
    {
        var presenter = this.AppWindow.Presenter as Microsoft.UI.Windowing.OverlappedPresenter;
        if (presenter != null)
        {
            presenter.IsMinimizable = false;
            presenter.IsMaximizable = false;
            presenter.IsResizable = false;
            this.AppWindow.TitleBar.IconShowOptions = Microsoft.UI.Windowing.IconShowOptions.HideIconAndSystemMenu;
        }
    }
    private void OnWindowClosed(object sender, WindowEventArgs args)
    {
        if (!ViewModel.WindowCloseConfirm)
        {
            args.Handled = true;
            IfWindowCloseTeachingTip.IsOpen = true;
            this.Activate();
        }
    }

    private void OnWindowCloseCancel(object sender, RoutedEventArgs e)
    {
        IfWindowCloseTeachingTip.IsOpen = false;
    }

    private void OnWindowCloseConfirm(object sender, RoutedEventArgs e)
    {
        ViewModel.CancelDownload();
        ViewModel.WindowCloseConfirm = true;
        this.Close();
        ViewModel.WindowCloseConfirm = false;
    }

}

