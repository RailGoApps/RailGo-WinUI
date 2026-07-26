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
    private readonly IPythonProcessManager _processManager;
    private readonly IAIChatClient _chatClient;
    private readonly List<NavigationViewItem> _conversationItems = new();
    private bool _loadingConversations;
    private Task? _backendStartTask;
    private int? _activeConversationId;
    private bool _hostEventsRegistered;

    public ShellViewModel ViewModel { get; }

    public ShellPage(
        ShellViewModel viewModel,
        IBackgroundImageService backgroundImageService,
        IPythonProcessManager processManager,
        IAIChatClient chatClient)
    {
        ViewModel = viewModel;
        _backgroundImageService = backgroundImageService;
        _processManager = processManager;
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
        KeyboardAccelerators.Add(BuildKeyboardAccelerator(VirtualKey.Left, VirtualKeyModifiers.Menu));
        KeyboardAccelerators.Add(BuildKeyboardAccelerator(VirtualKey.GoBack));

        _backgroundImageService.BackgroundImageChanged -= OnBackgroundImageChanged;
        _backgroundImageService.BackgroundImageChanged += OnBackgroundImageChanged;
        _ = ApplyBackgroundImageAsync(_backgroundImageService.BackgroundImagePath);

        _ = InitializeConversationNavigationAsync();
    }

    private void RegisterHostEvents()
    {
        if (_hostEventsRegistered)
            return;

        NavigationViewControl.ItemInvoked += OnHostItemInvoked;
        ViewModel.NavigationService.Navigated += OnHostNavigated;
        _processManager.StatusChanged += OnBackendStatusChanged;
        _chatClient.ConversationsChanged += OnConversationsChanged;
        _hostEventsRegistered = true;
    }

    private void UnregisterHostEvents()
    {
        if (!_hostEventsRegistered)
            return;

        NavigationViewControl.ItemInvoked -= OnHostItemInvoked;
        ViewModel.NavigationService.Navigated -= OnHostNavigated;
        _processManager.StatusChanged -= OnBackendStatusChanged;
        _chatClient.ConversationsChanged -= OnConversationsChanged;
        _hostEventsRegistered = false;
    }

    private async void OnHostItemInvoked(NavigationView sender, NavigationViewItemInvokedEventArgs args)
    {
        if (args.InvokedItemContainer is not NavigationViewItem item || item.Tag is not string tag)
            return;

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
            ViewModel.NavigationService.NavigateTo(navigationKey, cid);
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

    private void OnBackendStatusChanged(object? sender, bool running)
    {
        if (!running)
            return;
        DispatcherQueue.TryEnqueue(() => _ = LoadConversationItemsAsync());
    }

    private void OnConversationsChanged(object? sender, EventArgs e) =>
        DispatcherQueue.TryEnqueue(() => _ = LoadConversationItemsAsync());

    private async Task LoadConversationItemsAsync()
    {
        if (_loadingConversations || !_processManager.IsRunning)
            return;

        _loadingConversations = true;
        try
        {
            var conversations = await _chatClient.GetConversationsAsync();
            DispatcherQueue.TryEnqueue(() => RenderConversationItems(conversations));
        }
        finally
        {
            _loadingConversations = false;
        }
    }

    private async Task InitializeConversationNavigationAsync()
    {
        if (await EnsureBackendAsync())
            await LoadConversationItemsAsync();
    }

    private async Task<bool> EnsureBackendAsync()
    {
        if (_processManager.IsRunning)
            return true;

        _backendStartTask ??= _processManager.StartAsync();
        await _backendStartTask;
        if (!_processManager.IsRunning)
            _backendStartTask = null;
        return _processManager.IsRunning;
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
        rename.Click += async (_, _) => await RenameConversationAsync(conversation);

        var delete = new MenuFlyoutItem
        {
            Text = "删除",
            Icon = new FontIcon { Glyph = "\uE74D" },
        };
        delete.Click += async (_, _) => await DeleteConversationAsync(conversation);

        menu.Items.Add(rename);
        menu.Items.Add(delete);
        return menu;
    }

    private async Task RenameConversationAsync(ChatConversation conversation)
    {
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
