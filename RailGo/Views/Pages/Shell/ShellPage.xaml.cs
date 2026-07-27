using System.Collections.ObjectModel;
using RailGo.AI.Models;
using RailGo.AI.Services;
using RailGo.Contracts.Services;
using RailGo.Helpers;
using RailGo.ViewModels.Pages.Chat;
using RailGo.ViewModels.Pages.Shell;
using RailGo.Views.Pages.Chat;

using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;

using Windows.Storage;
using Windows.System;

namespace RailGo.Views.Pages.Shell;

public sealed partial class ShellPage : Page
{
    private readonly IBackgroundImageService _backgroundImageService;
    private readonly IRailGptRuntimeManager _runtimeManager;
    private readonly IRailGptStartupCoordinator _startupCoordinator;
    private readonly IRailGptConversationIndexReader _conversationIndexReader;
    private readonly IRailGptDiagnostics _diagnostics;
    private readonly IAIChatClient _chatClient;
    private readonly List<NavigationViewItem> _conversationItems = new();
    private bool _loadingConversations;
    private int? _activeConversationId;
    private bool _hostEventsRegistered;
    private bool _keyboardAcceleratorsRegistered;
    private bool _chatBusy;

    public ShellViewModel ViewModel { get; }

    public ShellPage(
        ShellViewModel viewModel,
        IBackgroundImageService backgroundImageService,
        IRailGptRuntimeManager runtimeManager,
        IRailGptStartupCoordinator startupCoordinator,
        IRailGptConversationIndexReader conversationIndexReader,
        IRailGptDiagnostics diagnostics,
        IAIChatClient chatClient)
    {
        ViewModel = viewModel;
        _backgroundImageService = backgroundImageService;
        _runtimeManager = runtimeManager;
        _startupCoordinator = startupCoordinator;
        _conversationIndexReader = conversationIndexReader;
        _diagnostics = diagnostics;
        _chatClient = chatClient;
        InitializeComponent();
        Unloaded += OnUnloaded;

        ViewModel.NavigationService.Frame = NavigationFrame;
        ViewModel.NavigationViewService.Initialize(NavigationViewControl);
        RegisterHostEvents();
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        // ShellPage can be unloaded and loaded again without being reconstructed.
        // Restore the RailGPT-specific handlers that OnUnloaded releases.
        RegisterHostEvents();

        TitleBarHelper.UpdateTitleBar(RequestedTheme);
        if (!_keyboardAcceleratorsRegistered)
        {
            KeyboardAccelerators.Add(BuildKeyboardAccelerator(VirtualKey.Left, VirtualKeyModifiers.Menu));
            KeyboardAccelerators.Add(BuildKeyboardAccelerator(VirtualKey.GoBack));
            _keyboardAcceleratorsRegistered = true;
        }

        _backgroundImageService.BackgroundImageChanged -= OnBackgroundImageChanged;
        _backgroundImageService.BackgroundImageChanged += OnBackgroundImageChanged;
        _ = ApplyBackgroundImageAsync(_backgroundImageService.BackgroundImagePath);

        _conversationIndexReader.Start();
        RenderConversationItems(_conversationIndexReader.LastKnownConversations);
        _ = RefreshConversationIndexObservedAsync();
    }

    private void RegisterHostEvents()
    {
        if (_hostEventsRegistered)
            return;

        NavigationViewControl.ItemInvoked += OnHostItemInvoked;
        ViewModel.NavigationService.Navigated += OnHostNavigated;
        _runtimeManager.StatusChanged += OnBackendStatusChanged;
        _conversationIndexReader.ConversationsChanged += OnIndexedConversationsChanged;
        _chatClient.ConversationsChanged += OnConversationsChanged;
        _chatClient.BusyChanged += OnChatBusyChanged;
        _hostEventsRegistered = true;
    }

    private void UnregisterHostEvents()
    {
        if (!_hostEventsRegistered)
            return;

        NavigationViewControl.ItemInvoked -= OnHostItemInvoked;
        ViewModel.NavigationService.Navigated -= OnHostNavigated;
        _runtimeManager.StatusChanged -= OnBackendStatusChanged;
        _conversationIndexReader.ConversationsChanged -= OnIndexedConversationsChanged;
        _chatClient.ConversationsChanged -= OnConversationsChanged;
        _chatClient.BusyChanged -= OnChatBusyChanged;
        _hostEventsRegistered = false;
    }

    private async void OnHostItemInvoked(NavigationView sender, NavigationViewItemInvokedEventArgs args)
    {
        try
        {
            if (args.InvokedItemContainer is not NavigationViewItem item || item.Tag is not string tag)
                return;

            if (_chatBusy && tag.StartsWith("chat:", StringComparison.OrdinalIgnoreCase))
            {
                await ShowBusyDialogAsync();
                return;
            }

            var navigationKey = typeof(ChatViewModel).FullName!;
            if (tag == "chat:new")
            {
                if (!await EnsureBackendAsync())
                    return;

                var conversation = await _chatClient.CreateConversationAsync();
                if (conversation == null)
                    return;

                await LoadConversationItemsAsync();
                ViewModel.NavigationService.NavigateTo(navigationKey, conversation.Id);
                return;
            }

            if (tag.StartsWith("chat:", StringComparison.OrdinalIgnoreCase) &&
                int.TryParse(tag[5..], out var cid))
            {
                // ChatPage awaits the shared startup task and shows a native
                // loading state if the warm-up has not completed yet.
                ViewModel.NavigationService.NavigateTo(navigationKey, cid);
            }
        }
        catch (Exception ex)
        {
            _diagnostics.Error("RailGPT navigation command failed.", ex);
        }
    }

    private void OnHostNavigated(object sender, Microsoft.UI.Xaml.Navigation.NavigationEventArgs e)
    {
        if (e.SourcePageType != typeof(ChatPage))
            return;

        if (e.Parameter is int cid)
        {
            _activeConversationId = cid;
            NavigationViewControl.SelectedItem = _conversationItems.FirstOrDefault(
                item => string.Equals(item.Tag?.ToString(), $"chat:{cid}", StringComparison.OrdinalIgnoreCase))
                ?? RailGPTNewChatNavItem;
        }
        else
        {
            _activeConversationId = null;
            NavigationViewControl.SelectedItem = RailGPTNewChatNavItem;
        }
    }

    private void OnBackendStatusChanged(object? sender, RailGptRuntimeStatus status)
    {
        if (!status.IsReady)
            return;
        DispatcherQueue.TryEnqueue(() => _ = LoadConversationItemsAsync());
    }

    private void OnConversationsChanged(object? sender, EventArgs e) =>
        DispatcherQueue.TryEnqueue(() => _ = LoadConversationItemsAsync());

    private void OnIndexedConversationsChanged(
        object? sender,
        IReadOnlyList<ChatConversation> conversations) =>
        DispatcherQueue.TryEnqueue(() => RenderConversationItems(conversations));

    private void OnChatBusyChanged(object? sender, bool busy) =>
        DispatcherQueue.TryEnqueue(() => _chatBusy = busy);

    private async Task LoadConversationItemsAsync()
    {
        if (_loadingConversations)
            return;

        _loadingConversations = true;
        try
        {
            var conversations = _runtimeManager.Status.IsReady
                ? await _chatClient.GetConversationsAsync()
                : (await _conversationIndexReader.RefreshAsync()).ToList();
            DispatcherQueue.TryEnqueue(() => RenderConversationItems(conversations));
        }
        catch (Exception ex)
        {
            _diagnostics.Error("Failed to refresh RailGPT conversations.", ex);
        }
        finally
        {
            _loadingConversations = false;
        }
    }

    private async Task RefreshConversationIndexObservedAsync()
    {
        try
        {
            await _conversationIndexReader.RefreshAsync();
        }
        catch (Exception ex)
        {
            _diagnostics.Error("Failed to load native RailGPT conversation index.", ex);
        }
    }

    private async Task<bool> EnsureBackendAsync()
    {
        var result = await _startupCoordinator.StartAsync();
        return result.Success;
    }

    private async Task ShowBusyDialogAsync()
    {
        var dialog = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = "RailGPT 正在生成回答",
            Content = "请等待当前回答完成后再新建、删除或切换会话。",
            CloseButtonText = "知道了",
        };
        await dialog.ShowAsync();
    }

    private void RenderConversationItems(IReadOnlyList<ChatConversation> conversations)
    {
        foreach (var item in _conversationItems)
            NavigationViewControl.MenuItems.Remove(item);
        _conversationItems.Clear();

        var insertIndex = NavigationViewControl.MenuItems.IndexOf(RailGPTHistoryHeader) + 1;
        foreach (var conversation in conversations.OrderByDescending(c => c.UpdatedAt))
        {
            var item = new NavigationViewItem
            {
                Content = string.IsNullOrWhiteSpace(conversation.Title) ? "新对话" : conversation.Title,
                Tag = $"chat:{conversation.Id}",
            };
            ToolTipService.SetToolTip(item, conversation.Title);
            item.ContextFlyout = BuildConversationMenu(conversation);
            NavigationViewControl.MenuItems.Insert(insertIndex++, item);
            _conversationItems.Add(item);
        }

        RailGPTHistoryHeader.Visibility = _conversationItems.Count > 0
            ? Visibility.Visible
            : Visibility.Collapsed;

        if (_activeConversationId is int activeId)
            NavigationViewControl.SelectedItem = _conversationItems.FirstOrDefault(
                item => string.Equals(item.Tag?.ToString(), $"chat:{activeId}", StringComparison.OrdinalIgnoreCase));
    }

    private MenuFlyout BuildConversationMenu(ChatConversation conversation)
    {
        var menu = new MenuFlyout();
        var rename = new MenuFlyoutItem
        {
            Text = "重命名",
            Icon = new FontIcon { Glyph = "\uE70F" },
        };
        rename.Click += (_, _) =>
            _ = ExecuteConversationCommandObservedAsync(() => RenameConversationAsync(conversation));

        var delete = new MenuFlyoutItem
        {
            Text = "删除",
            Icon = new FontIcon { Glyph = "\uE74D" },
        };
        delete.Click += (_, _) =>
            _ = ExecuteConversationCommandObservedAsync(() => DeleteConversationAsync(conversation));

        menu.Items.Add(rename);
        menu.Items.Add(delete);
        return menu;
    }

    private async Task ExecuteConversationCommandObservedAsync(Func<Task> command)
    {
        try
        {
            await command();
        }
        catch (Exception ex)
        {
            _diagnostics.Error("RailGPT conversation command failed.", ex);
        }
    }

    private async Task RenameConversationAsync(ChatConversation conversation)
    {
        if (_chatBusy)
        {
            await ShowBusyDialogAsync();
            return;
        }
        if (!await EnsureBackendAsync())
            return;

        var input = new TextBox
        {
            Text = conversation.Title,
            PlaceholderText = "对话名称",
            SelectionStart = conversation.Title.Length,
        };
        var dialog = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = "重命名对话",
            Content = input,
            PrimaryButtonText = "保存",
            CloseButtonText = "取消",
            DefaultButton = ContentDialogButton.Primary,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(input.Text))
            return;

        if (await _chatClient.RenameConversationAsync(conversation.Id, input.Text.Trim()))
            await LoadConversationItemsAsync();
    }

    private async Task DeleteConversationAsync(ChatConversation conversation)
    {
        if (_chatBusy)
        {
            await ShowBusyDialogAsync();
            return;
        }
        if (!await EnsureBackendAsync())
            return;

        var title = string.IsNullOrWhiteSpace(conversation.Title) ? "新对话" : conversation.Title;
        var dialog = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = "删除对话？",
            Content = $"“{title}”将被永久删除。",
            PrimaryButtonText = "删除",
            CloseButtonText = "取消",
            DefaultButton = ContentDialogButton.Close,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
            return;

        if (!await _chatClient.DeleteConversationAsync(conversation.Id))
            return;

        var deletedActiveConversation = _activeConversationId == conversation.Id;
        if (deletedActiveConversation)
            _activeConversationId = null;
        await LoadConversationItemsAsync();
        if (deletedActiveConversation)
            NavigationViewControl.SelectedItem = RailGPTNewChatNavItem;
    }

    private static KeyboardAccelerator BuildKeyboardAccelerator(VirtualKey key, VirtualKeyModifiers? modifiers = null)
    {
        var accelerator = new KeyboardAccelerator { Key = key };
        if (modifiers.HasValue) accelerator.Modifiers = modifiers.Value;
        accelerator.Invoked += OnKeyboardAcceleratorInvoked;
        return accelerator;
    }

    private static void OnKeyboardAcceleratorInvoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args)
    {
        args.Handled = App.GetService<INavigationService>().GoBack();
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        _backgroundImageService.BackgroundImageChanged -= OnBackgroundImageChanged;
        UnregisterHostEvents();
    }

    private void OnBackgroundImageChanged(object? sender, string? imagePath)
    {
        if (DispatcherQueue.HasThreadAccess)
        {
            _ = ApplyBackgroundImageAsync(imagePath);
            return;
        }
        DispatcherQueue.TryEnqueue(() => _ = ApplyBackgroundImageAsync(imagePath));
    }

    private async Task ApplyBackgroundImageAsync(string? imagePath)
    {
        if (string.IsNullOrWhiteSpace(imagePath) || !File.Exists(imagePath))
        {
            RootGrid.Background = null;
            return;
        }

        try
        {
            var file = await StorageFile.GetFileFromPathAsync(imagePath);
            using var stream = await file.OpenReadAsync();
            var bitmapImage = new BitmapImage();
            await bitmapImage.SetSourceAsync(stream);
            RootGrid.Background = new ImageBrush
            {
                ImageSource = bitmapImage,
                Stretch = Stretch.UniformToFill,
                Opacity = 0.25,
                AlignmentX = AlignmentX.Center,
                AlignmentY = AlignmentY.Center,
            };
        }
        catch
        {
            RootGrid.Background = null;
        }
    }
}
