using System;
using System.IO;
using System.IO.Compression;
using System.Threading.Tasks;
using RailGo.Core.Models.Settings;
using RailGo.Core.Helpers;

namespace RailGo.Core.Query.Online;

public class DBGetService
{
    private const string ApiBaseUrl = "https://api.state.railgo.zenglingkun.cn/api/v1";

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
            // 1. 获取下载地址
            progress?.Report(new DownloadProgress { Status = "正在获取下载地址...", Percentage = 0 });
            var downloadUrl = await GetDatabaseDownloadUrlAsync();
            if (string.IsNullOrEmpty(downloadUrl))
            {
                throw new Exception("无法获取数据库下载地址");
            }

            // 2. 下载ZIP文件
            progress?.Report(new DownloadProgress { Status = "正在下载数据库文件...", Percentage = 25 });
            var zipData = await HttpService.DownloadFileAsync(downloadUrl);
            progress?.Report(new DownloadProgress { Status = "下载完成，正在解压...", Percentage = 50 });

            // 3. 解压ZIP文件
            var databasePath = await ExtractDatabaseFromZip(zipData, customDownloadPath);
            progress?.Report(new DownloadProgress { Status = "数据库更新完成", Percentage = 100 });

            return File.Exists(databasePath);
        }
        catch (Exception ex)
        {
            throw new Exception($"数据库下载失败: {ex.Message}", ex);
        }
    }

    /// <summary>
    /// 从ZIP数据中提取数据库文件
    /// </summary>
    private static async Task<string> ExtractDatabaseFromZip(byte[] zipData, string customDownloadPath = null)
    {
        // 确定数据库保存路径
        string databaseDirectory;
        if (!string.IsNullOrEmpty(customDownloadPath))
        {
            databaseDirectory = Path.GetDirectoryName(customDownloadPath);
        }
        else
        {
            // 使用应用程序所在目录的 Data 文件夹
            var appDirectory = AppContext.BaseDirectory;
            databaseDirectory = Path.Combine(appDirectory, "ApplicationData");
        }

        // 确保目录存在
        if (!Directory.Exists(databaseDirectory))
        {
            Directory.CreateDirectory(databaseDirectory);
        }

        // 临时ZIP文件路径
        var tempZipPath = Path.Combine(Path.GetTempPath(), $"railgo_temp_{Guid.NewGuid()}.zip");
        var extractDirectory = Path.Combine(Path.GetTempPath(), $"railgo_extract_{Guid.NewGuid()}");

        try
        {
            // 1. 保存ZIP文件到临时位置
            await File.WriteAllBytesAsync(tempZipPath, zipData);

            // 2. 创建解压目录
            Directory.CreateDirectory(extractDirectory);

            // 3. 解压ZIP文件
            ZipFile.ExtractToDirectory(tempZipPath, extractDirectory);

            // 4. 查找数据库文件
            var databaseFile = FindDatabaseFile(extractDirectory);
            if (databaseFile == null)
            {
                throw new FileNotFoundException("在ZIP文件中未找到 railgo.sqlite 数据库文件");
            }

            // 5. 确定最终数据库路径
            var finalDatabasePath = string.IsNullOrEmpty(customDownloadPath)
                ? Path.Combine(databaseDirectory, "railgo.sqlite")
                : customDownloadPath;

            // 6. 复制数据库文件到目标位置
            File.Copy(databaseFile, finalDatabasePath, true);

            return finalDatabasePath;
        }
        finally
        {
            // 清理临时文件
            try
            {
                if (File.Exists(tempZipPath))
                    File.Delete(tempZipPath);

                if (Directory.Exists(extractDirectory))
                    Directory.Delete(extractDirectory, true);
            }
            catch
            {
                // 忽略清理错误
            }
        }
    }

    /// <summary>
    /// 在解压目录中查找数据库文件
    /// </summary>
    private static string FindDatabaseFile(string extractDirectory)
    {
        // 首先查找根目录
        var rootDbFile = Path.Combine(extractDirectory, "railgo.sqlite");
        if (File.Exists(rootDbFile))
        {
            return rootDbFile;
        }

        // 递归查找所有子目录
        var allFiles = Directory.GetFiles(extractDirectory, "railgo.sqlite", SearchOption.AllDirectories);
        return allFiles.Length > 0 ? allFiles[0] : null;
    }

    /// <summary>
    /// 获取本地数据库路径
    /// </summary>
    public static string GetLocalDatabasePath(string customPath = null)
    {
        if (!string.IsNullOrEmpty(customPath))
        {
            return customPath;
        }

        var appDirectory = AppContext.BaseDirectory;
        return Path.Combine(appDirectory, "ApplicationData", "railgo.sqlite");
    }

    /// <summary>
    /// 检查本地数据库是否存在
    /// </summary>
    public static bool LocalDatabaseExists(string customPath = null)
    {
        var databasePath = GetLocalDatabasePath(customPath);
        return File.Exists(databasePath);
    }

    /// <summary>
    /// 获取本地数据库文件信息
    /// </summary>
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

    /// <summary>
    /// 删除本地数据库文件
    /// </summary>
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