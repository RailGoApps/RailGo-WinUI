using System.Collections.ObjectModel;
using System.Linq;
using System.Threading.Tasks;
using Newtonsoft.Json;
using RailGo.Contracts.Services;
using RailGo.Core.Models.Settings;

namespace RailGo.Services;

public class DataSourceService : IDataSourceService
{
    private const string DataSourcesSettingsKey = "DataSources";
    private const string SelectedDataSourceSettingsKey = "DataSourcesSettings_SelectedDataSource";
    private const string LocalDatabaseSourcesKey = "DataSourcesSettings_LocalDatabaseSources";
    private const string OnlineApiSourcesKey = "DataSourcesSettings_OnlineApiSources";
    private const string QueryModeSettingsKey = "DataSourcesSettings_QueryMode";
    private const string OfflineDatabaseVersionKey = "DataSourcesSettings_OfflineDatabaseVersion";

    private const string IfAllowCustomSourceKey = "DataSourcesSettings_IfAllowCustomSource";
    private const string CustomDataSourceAddressKey = "DataSourcesSettings_CustomDataSourceAddress";
    private const string OfflineComplementOnlineKey = "DataSourcesSettings_OfflineComplementOnline";
    private const string OnlineFallbackToOfflineKey = "DataSourcesSettings_OnlineFallbackToOffline";

    private readonly ILocalSettingsService _localSettingsService;

    private ObservableCollection<DataSourceGroup> _dataSources = new ObservableCollection<DataSourceGroup>();

    public DataSourceService(ILocalSettingsService localSettingsService)
    {
        _localSettingsService = localSettingsService;
    }

    public async Task InitializeAsync()
    {
        var dataSources = await LoadDataSourcesFromSettingsAsync();
        _dataSources = new ObservableCollection<DataSourceGroup>(dataSources);
        await Task.CompletedTask;
    }

    #region 读取操作

    public async Task<ObservableCollection<DataSourceGroup>> GetAllDataSourcesAsync()
    {
        await InitializeAsync();
        return _dataSources;
    }

    public async Task<DataSourceGroup?> GetDataSourceGroupAsync(string groupName)
    {
        await InitializeAsync();
        return _dataSources.FirstOrDefault(g => g.Name == groupName);
    }

    public async Task<DataSourceMethod?> GetDataSourceMethodAsync(string groupName, string methodName)
    {
        var group = await GetDataSourceGroupAsync(groupName);
        return group?.Data.FirstOrDefault(m => m.Name == methodName);
    }

    #endregion

    #region 读取所有值的方法

    public async Task<ObservableCollection<DataSourceMethod>> GetAllMethodsAsync()
    {
        await InitializeAsync();

        var allMethods = new ObservableCollection<DataSourceMethod>();
        foreach (var group in _dataSources)
        {
            foreach (var method in group.Data)
            {
                allMethods.Add(method);
            }
        }

        return allMethods;
    }

    public async Task<ObservableCollection<string>> GetAllGroupNamesAsync()
    {
        await InitializeAsync();
        return new ObservableCollection<string>(_dataSources.Select(g => g.Name));
    }

    public async Task<ObservableCollection<DataSourceMethod>> GetMethodsByModeAsync(string mode)
    {
        await InitializeAsync();

        var methods = new ObservableCollection<DataSourceMethod>();
        foreach (var group in _dataSources)
        {
            foreach (var method in group.Data.Where(m => m.Mode == mode))
            {
                methods.Add(method);
            }
        }

        return methods;
    }

    #endregion

    #region 设置操作

    public async Task SetDataSourceGroupAsync(DataSourceGroup group)
    {
        await InitializeAsync();

        var existingGroup = _dataSources.FirstOrDefault(g => g.Name == group.Name);
        if (existingGroup != null)
        {
            _dataSources.Remove(existingGroup);
        }

        _dataSources.Add(group);
        await SaveDataSourcesToSettingsAsync(_dataSources);
    }

    public async Task SetDataSourceMethodAsync(string groupName, DataSourceMethod method)
    {
        await InitializeAsync();

        var group = _dataSources.FirstOrDefault(g => g.Name == groupName);
        if (group == null)
        {
            group = new DataSourceGroup { Name = groupName };
            _dataSources.Add(group);
        }

        var existingMethod = group.Data.FirstOrDefault(m => m.Name == method.Name);
        if (existingMethod != null)
        {
            group.Data.Remove(existingMethod);
        }

        group.Data.Add(method);
        await SaveDataSourcesToSettingsAsync(_dataSources);
    }

    public async Task UpdateDataSourceMethodAsync(string groupName, string methodName, string mode, string sourceName)
    {
        var method = new DataSourceMethod
        {
            Name = methodName,
            Mode = mode,
            SourceName = sourceName  // 改为存储源名称
        };

        await SetDataSourceMethodAsync(groupName, method);
    }

    #endregion

    #region 选择的DataSource

    public async Task<string?> GetSelectedDataSourceAsync()
    {
        return await _localSettingsService.ReadSettingAsync<string>(SelectedDataSourceSettingsKey);
    }

    public async Task SetSelectedDataSourceAsync(string selectedDataSource)
    {
        await _localSettingsService.SaveSettingAsync(SelectedDataSourceSettingsKey, selectedDataSource);
    }

    public async Task<DataSourceGroup?> GetSelectedDataSourceGroupAsync()
    {
        var selectedName = await GetSelectedDataSourceAsync();
        return string.IsNullOrEmpty(selectedName) ? null : await GetDataSourceGroupAsync(selectedName);
    }

    public async Task<DataSourceMethod?> GetSelectedDataSourceMethodAsync(string methodName)
    {
        var selectedName = await GetSelectedDataSourceAsync();
        return string.IsNullOrEmpty(selectedName) ? null : await GetDataSourceMethodAsync(selectedName, methodName);
    }

    #endregion

    #region 本地数据库源管理
    public async Task<ObservableCollection<LocalDatabaseSource>> GetLocalDatabaseSourcesAsync()
    {
        var sources = await _localSettingsService.ReadSettingAsync<ObservableCollection<LocalDatabaseSource>>(LocalDatabaseSourcesKey);
        return sources ?? new ObservableCollection<LocalDatabaseSource>();
    }

    public async Task SaveLocalDatabaseSourceAsync(LocalDatabaseSource source)
    {
        var sources = await GetLocalDatabaseSourcesAsync();
        var existing = sources.FirstOrDefault(s => s.Name == source.Name);
        if (existing != null)
        {
            sources.Remove(existing);
        }
        sources.Add(source);
        await _localSettingsService.SaveSettingAsync(LocalDatabaseSourcesKey, sources);
    }

    public async Task DeleteLocalDatabaseSourceAsync(string sourceName)
    {
        var sources = await GetLocalDatabaseSourcesAsync();
        var existing = sources.FirstOrDefault(s => s.Name == sourceName);
        if (existing != null)
        {
            sources.Remove(existing);
            await _localSettingsService.SaveSettingAsync(LocalDatabaseSourcesKey, sources);
        }
    }

    public async Task<string?> GetLocalDatabaseSourceAddressAsync(string sourceName)
    {
        var sources = await GetLocalDatabaseSourcesAsync();
        var source = sources.FirstOrDefault(s => s.Name == sourceName);
        return source?.Address;
    }
    #endregion

    #region 在线API源管理
    public async Task<ObservableCollection<OnlineApiSource>> GetOnlineApiSourcesAsync()
    {
        var sources = await _localSettingsService.ReadSettingAsync<ObservableCollection<OnlineApiSource>>(OnlineApiSourcesKey);
        return sources ?? new ObservableCollection<OnlineApiSource>();
    }

    public async Task SaveOnlineApiSourceAsync(OnlineApiSource source)
    {
        var sources = await GetOnlineApiSourcesAsync();
        var existing = sources.FirstOrDefault(s => s.Name == source.Name);
        if (existing != null)
        {
            sources.Remove(existing);
        }
        sources.Add(source);
        await _localSettingsService.SaveSettingAsync(OnlineApiSourcesKey, sources);
    }

    public async Task DeleteOnlineApiSourceAsync(string sourceName)
    {
        var sources = await GetOnlineApiSourcesAsync();
        var existing = sources.FirstOrDefault(s => s.Name == sourceName);
        if (existing != null)
        {
            sources.Remove(existing);
            await _localSettingsService.SaveSettingAsync(OnlineApiSourcesKey, sources);
        }
    }

    public async Task<string?> GetOnlineApiSourceAddressAsync(string sourceName)
    {
        var sources = await GetOnlineApiSourcesAsync();
        var source = sources.FirstOrDefault(s => s.Name == sourceName);
        return source?.Address;
    }
    #endregion

    #region 源地址查询方法

    /// <summary>
    /// 根据方法获取实际的数据源地址
    /// </summary>
    public async Task<string?> GetDataSourceAddressAsync(DataSourceMethod method)
    {
        if (method == null) return null;

        return method.Mode.ToLower() switch
        {
            "online" => await GetOnlineApiSourceAddressAsync(method.SourceName),
            "offline" => await GetLocalDatabaseSourceAddressAsync(method.SourceName),
            _ => null
        };
    }

    /// <summary>
    /// 根据组名和方法名获取实际的数据源地址
    /// </summary>
    public async Task<string?> GetDataSourceAddressAsync(string groupName, string methodName)
    {
        var method = await GetDataSourceMethodAsync(groupName, methodName);
        return await GetDataSourceAddressAsync(method);
    }

    /// <summary>
    /// 根据选中的数据源和方法名获取实际的数据源地址
    /// </summary>
    public async Task<string?> GetSelectedDataSourceAddressAsync(string methodName)
    {
        var method = await GetSelectedDataSourceMethodAsync(methodName);
        return await GetDataSourceAddressAsync(method);
    }

    #endregion

    #region 离线数据库版本管理
    public async Task<OfflineDatabaseVersion?> GetOfflineDatabaseVersionAsync()
    {
        return await _localSettingsService.ReadSettingAsync<OfflineDatabaseVersion>(OfflineDatabaseVersionKey);
    }

    public async Task SetOfflineDatabaseVersionAsync(OfflineDatabaseVersion version)
    {
        await _localSettingsService.SaveSettingAsync(OfflineDatabaseVersionKey, version);
    }

    public async Task UpdateOfflineDatabaseVersionAsync(string version, int sequence)
    {
        var dbVersion = new OfflineDatabaseVersion
        {
            InstallDate = DateTime.Now,
            Version = version,
            Sequence = sequence
        };
        await SetOfflineDatabaseVersionAsync(dbVersion);
    }
    #endregion

    #region 自定义数据源管理

    public async Task<bool> GetIfAllowCustomSourceAsync()
    {
        return await _localSettingsService.ReadSettingAsync<bool>(IfAllowCustomSourceKey);
    }

    public async Task SetIfAllowCustomSourceAsync(bool allowCustomSource)
    {
        await _localSettingsService.SaveSettingAsync(IfAllowCustomSourceKey, allowCustomSource);
    }

    public async Task<string?> GetCustomDataSourceAddressAsync()
    {
        return await _localSettingsService.ReadSettingAsync<string>(CustomDataSourceAddressKey);
    }

    public async Task SetCustomDataSourceAddressAsync(string address)
    {
        await _localSettingsService.SaveSettingAsync(CustomDataSourceAddressKey, address);
    }

    #endregion

    #region 互补查询设置

    public async Task<bool> GetOfflineComplementOnlineAsync()
    {
        return await _localSettingsService.ReadSettingAsync<bool>(OfflineComplementOnlineKey);
    }

    public async Task SetOfflineComplementOnlineAsync(bool value)
    {
        await _localSettingsService.SaveSettingAsync(OfflineComplementOnlineKey, value);
    }

    public async Task<bool> GetOnlineFallbackToOfflineAsync()
    {
        return await _localSettingsService.ReadSettingAsync<bool>(OnlineFallbackToOfflineKey);
    }

    public async Task SetOnlineFallbackToOfflineAsync(bool value)
    {
        await _localSettingsService.SaveSettingAsync(OnlineFallbackToOfflineKey, value);
    }

    #endregion

    #region 查询模式管理
    public async Task<string?> GetQueryModeAsync()
    {
        var queryMode = await _localSettingsService.ReadSettingAsync<string>(QueryModeSettingsKey);
        return queryMode ?? "Online";
    }
    public async Task SetQueryModeAsync(string queryMode)
    {
        await _localSettingsService.SaveSettingAsync(QueryModeSettingsKey, queryMode);
    }
    #endregion

    #region 辅助方法

    private async Task<List<DataSourceGroup>> LoadDataSourcesFromSettingsAsync()
    {
        var dataSources = await _localSettingsService.ReadSettingAsync<List<DataSourceGroup>>(DataSourcesSettingsKey);
        return dataSources ?? new List<DataSourceGroup>();
    }

    public async Task SaveDataSourcesToSettingsAsync(ObservableCollection<DataSourceGroup> dataSources)
    {
        await _localSettingsService.SaveSettingAsync(DataSourcesSettingsKey, dataSources.ToList());
    }

    #endregion
}