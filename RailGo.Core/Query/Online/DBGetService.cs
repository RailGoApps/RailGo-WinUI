using System;
using System.IO;
using System.IO.Compression;
using System.Threading.Tasks;
using RailGo.Core.Models.Settings;
using RailGo.Core.Helpers;

namespace RailGo.Core.Query.Online;

public class DBGetService
{
    private const string ApiBaseUrl = "https://api.state.railgo.zenglingkun.cn/api/v2";

    /// <summary>
    /// 获取版本信息
    /// </summary>
    public static async Task<VersionInfo> GetVersionInfoAsync()
    {
        var url = $"{ApiBaseUrl}/info";
        return await HttpService.GetAsync<VersionInfo>(url);
    }

    /// <summary>
    /// 获取离线数据库下载地址
    /// </summary>
    public static async Task<string> GetDatabaseDownloadUrlAsync()
    {
        var url = $"{ApiBaseUrl}/url/db";
        var response = await HttpService.GetAsync<DownloadUrlResponse>(url);
        return response?.Url;
    }

    /// <summary>
    /// 下载并保存离线数据库
    /// </summary>
    public static async Task<bool> DownloadAndSaveDatabaseAsync(IProgress<DownloadProgress> progress = null, string customDownloadPath = null)
    {
        try
        {
            progress?.Report(new DownloadProgress { Status = "正在获取下载地址...", Percentage = 0 });
            var downloadUrl = await GetDatabaseDownloadUrlAsync();
            if (string.IsNullOrEmpty(downloadUrl))
            {
                throw new Exception("无法获取数据库下载地址");
            }

            progress?.Report(new DownloadProgress { Status = "正在下载数据库文件...", Percentage = 25 });
            var zipData = await HttpService.DownloadFileAsync(downloadUrl);
            progress?.Report(new DownloadProgress { Status = "下载完成，正在解压...", Percentage = 50 });

            var databasePath = await ExtractDatabaseFromZip(zipData, customDownloadPath);
            progress?.Report(new DownloadProgress { Status = "数据库更新完成", Percentage = 100 });

            return File.Exists(databasePath);
        }
        catch (Exception ex)
        {
            throw new Exception($"数据库下载失败: {ex.Message}", ex);
        }
    }

    private static async Task<string> ExtractDatabaseFromZip(byte[] zipData, string customDownloadPath = null)
    {
        string databaseDirectory;
        if (!string.IsNullOrEmpty(customDownloadPath))
        {
            databaseDirectory = Path.GetDirectoryName(customDownloadPath);
        }
        else
        {
            var appDirectory = AppContext.BaseDirectory;
            databaseDirectory = Path.Combine(appDirectory, "ApplicationData");
        }

        if (!Directory.Exists(databaseDirectory))
        {
            Directory.CreateDirectory(databaseDirectory);
        }

        var tempZipPath = Path.Combine(Path.GetTempPath(), $"railgo_temp_{Guid.NewGuid()}.zip");
        var extractDirectory = Path.Combine(Path.GetTempPath(), $"railgo_extract_{Guid.NewGuid()}");

        try
        {
            await File.WriteAllBytesAsync(tempZipPath, zipData);
            Directory.CreateDirectory(extractDirectory);
            ZipFile.ExtractToDirectory(tempZipPath, extractDirectory);

            var databaseFile = FindDatabaseFile(extractDirectory);
            if (databaseFile == null)
            {
                throw new FileNotFoundException("在ZIP文件中未找到 railgo.sqlite 数据库文件");
            }

            var finalDatabasePath = string.IsNullOrEmpty(customDownloadPath)
                ? Path.Combine(databaseDirectory, "railgo.sqlite")
                : customDownloadPath;

            File.Copy(databaseFile, finalDatabasePath, true);
            return finalDatabasePath;
        }
        finally
        {
            try
            {
                if (File.Exists(tempZipPath))
                    File.Delete(tempZipPath);
                if (Directory.Exists(extractDirectory))
                    Directory.Delete(extractDirectory, true);
            }
            catch { }
        }
    }

    private static string FindDatabaseFile(string extractDirectory)
    {
        var rootDbFile = Path.Combine(extractDirectory, "railgo.sqlite");
        if (File.Exists(rootDbFile))
        {
            return rootDbFile;
        }
        var allFiles = Directory.GetFiles(extractDirectory, "railgo.sqlite", SearchOption.AllDirectories);
        return allFiles.Length > 0 ? allFiles[0] : null;
    }

    public static string GetLocalDatabasePath(string customPath = null)
    {
        if (!string.IsNullOrEmpty(customPath))
        {
            return customPath;
        }
        var appDirectory = AppContext.BaseDirectory;
        return Path.Combine(appDirectory, "ApplicationData", "railgo.sqlite");
    }

    public static bool LocalDatabaseExists(string customPath = null)
    {
        var databasePath = GetLocalDatabasePath(customPath);
        return File.Exists(databasePath);
    }

    public static async Task<DatabaseInfo> GetLocalDatabaseInfoAsync(string customPath = null)
    {
        var databasePath = GetLocalDatabasePath(customPath);
        if (File.Exists(databasePath))
        {
            var fileInfo = new FileInfo(databasePath);
            return new DatabaseInfo
            {
                Path = databasePath,
                FileSize = fileInfo.Length,
                LastModified = fileInfo.LastWriteTime,
                Exists = true
            };
        }
        return new DatabaseInfo { Exists = false };
    }

    public static bool DeleteLocalDatabase(string customPath = null)
    {
        try
        {
            var databasePath = GetLocalDatabasePath(customPath);
            if (File.Exists(databasePath))
            {
                File.Delete(databasePath);
                return true;
            }
            return false;
        }
        catch
        {
            return false;
        }
    }
}
