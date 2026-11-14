using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Management;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Markup;
using Microsoft.Win32;
using Forms = System.Windows.Forms;

[assembly: AssemblyTitle("Local AI Music Stem Separator Setup")]
[assembly: AssemblyProduct("Local AI Music Stem Separator")]
[assembly: AssemblyVersion(ReleaseInfo.AssemblyVersion)]
[assembly: AssemblyFileVersion(ReleaseInfo.AssemblyVersion)]

internal static class Setup
{
    private const string Product = "Local AI Music Stem Separator";
    private const string PythonVersion = "3.11.18";
    private const string TorchVersion = "2.11.0";
    private const string Channel = "cu128";
    private const string Ready = ".installed";
    private static readonly string Local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
    private static readonly string Programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);

    [STAThread]
    public static int Main(string[] args)
    {
        if (args.Length >= 2 && args[0] == "--install")
        {
            try
            {
                Install(args[1], args.Contains("--cuda"), !args.Contains("--skip-models"),
                    (step, label) => Console.WriteLine(label), CancellationToken.None, !args.Contains("--no-register"));
                return 0;
            }
            catch (Exception error)
            {
                File.WriteAllText(LogPath(), error.ToString());
                return 1;
            }
        }
        try
        {
            var app = new Application();
            var window = (Window)XamlReader.Load(Resource("Setup.xaml"));
            var wizard = new Wizard(window, args.Length == 2 && args[0] == "--uninstall" ? args[1] : null);
            app.Run(window);
            GC.KeepAlive(wizard);
            return 0;
        }
        catch (Exception error)
        {
            File.WriteAllText(LogPath(), error.ToString());
            return 1;
        }
    }

    private static Stream Resource(string name)
    {
        return Assembly.GetExecutingAssembly().GetManifestResourceStream(name);
    }

    private static string LogPath()
    {
        return Path.Combine(Path.GetTempPath(), "StemSeparator-Setup.log");
    }

    private static string GetRoot(string parent)
    {
        return Path.Combine(Path.GetFullPath(parent), Product);
    }

    private static string Python(string runtime, bool windowed)
    {
        return Path.Combine(runtime, "environment", "Scripts", windowed ? "pythonw.exe" : "python.exe");
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private static void Install(string parent, bool cuda, bool models, Action<int, string> progress,
                                CancellationToken cancellation, bool register = true)
    {
        if (!Environment.Is64BitOperatingSystem)
            throw new InvalidOperationException("此安装包需要 64 位 Windows。");
        string root = GetRoot(parent);
        Directory.CreateDirectory(root);
        using (new FileStream(Path.Combine(root, ".setup-lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None))
        {
            string runtime = Path.Combine(root, "runtime-" + ReleaseInfo.Version + (cuda ? "-cuda" : "-cpu"));
            bool created = !Directory.Exists(runtime);
            if (!created && !File.Exists(Path.Combine(runtime, Ready)))
                throw new IOException("检测到未完成的安装目录，请换一个安装位置。\n" + runtime);
            try
            {
                if (created)
                {
                    progress(1, "正在展开安装文件…");
                    Directory.CreateDirectory(runtime);
                    using (var archive = new ZipArchive(Resource("payload.zip"), ZipArchiveMode.Read))
                        archive.ExtractToDirectory(runtime);
                    string uv = Path.Combine(runtime, "uv.exe");
                    var environment = new Dictionary<string, string> {
                        {"UV_PYTHON_INSTALL_DIR", Path.Combine(runtime, "python")},
                        {"UV_PYTHON_PREFERENCE", "only-managed"},
                        {"UV_NO_PROGRESS", "1"},
                        {"UV_NO_CACHE", "1"}
                    };
                    progress(2, "正在准备 Python 运行环境…");
                    Run(uv, "python install " + PythonVersion + " --no-bin", runtime, environment, cancellation);
                    Run(uv, "venv --python " + PythonVersion + " environment", runtime, environment, cancellation);
                    progress(3, cuda ? "正在安装 NVIDIA CUDA 推理运行库…" : "正在安装 CPU 推理运行库…");
                    string python = Python(runtime, false);
                    string pip = "pip install --python " + Quote(python);
                    string index = "https://download.pytorch.org/whl/" + (cuda ? Channel : "cpu");
                    Run(uv, pip + " --index-url " + index + " torch==" + TorchVersion + " torchaudio==" + TorchVersion,
                        runtime, environment, cancellation);
                    progress(4, "正在安装桌面应用与音频组件…");
                    string wheel = Directory.GetFiles(runtime, "local_stem_separator-*.whl").Single();
                    Run(uv, pip + " " + Quote(wheel + "[gui]"), runtime, environment, cancellation);
                    Run(python, "-c \"import torch, torchaudio, sounddevice, soundfile; from stem_separator.gui.app import MainWindow\"",
                        runtime, null, cancellation);
                    File.Delete(wheel);
                }
                if (models)
                {
                    progress(5, "正在下载并校验本地模型。首次安装需要几 GB 网络流量，请保持连接。");
                    Run(Python(runtime, false), "-m stem_separator.cli --model-dir " +
                        Quote(Path.Combine(root, ".model-cache")) + " setup-model --all", root, null, cancellation);
                }
                cancellation.ThrowIfCancellationRequested();
                progress(6, "正在创建开始菜单快捷方式…");
                Directory.CreateDirectory(Path.Combine(root, ".model-cache"));
                if (register) Register(root, runtime);
                File.WriteAllText(Path.Combine(runtime, Ready), ReleaseInfo.Version);
            }
            catch
            {
                if (created && Directory.Exists(runtime))
                    Directory.Delete(runtime, true);
                throw;
            }
        }
    }

    private static void Register(string root, string runtime)
    {
        string id = InstallId(root);
        string uninstall = Path.Combine(Local, Product + " Setup", id + ".exe");
        Directory.CreateDirectory(Path.GetDirectoryName(uninstall));
        File.Copy(Assembly.GetExecutingAssembly().Location, uninstall, true);
        string link = Path.Combine(Programs, Product + ".lnk");
        object shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
        dynamic shortcut = ((dynamic)shell).CreateShortcut(link);
        try
        {
            shortcut.TargetPath = Python(runtime, true);
            shortcut.Arguments = "-m stem_separator.gui.app --model-dir " + Quote(Path.Combine(root, ".model-cache"));
            shortcut.WorkingDirectory = root;
            shortcut.Description = "音轨分离";
            shortcut.Save();
        }
        finally
        {
            Marshal.FinalReleaseComObject(shortcut);
            Marshal.FinalReleaseComObject(shell);
        }
        using (RegistryKey key = Registry.CurrentUser.CreateSubKey(UninstallKey(id)))
        {
            key.SetValue("DisplayName", Product);
            key.SetValue("DisplayVersion", ReleaseInfo.Version);
            key.SetValue("InstallLocation", root);
            key.SetValue("UninstallString", Quote(uninstall) + " --uninstall " + Quote(root));
            key.SetValue("NoModify", 1);
            key.SetValue("NoRepair", 1);
        }
    }

    private static string InstallId(string root)
    {
        using (var sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(root.ToUpperInvariant()))).Replace("-", "").Substring(0, 16);
    }

    private static string UninstallKey(string id)
    {
        return @"Software\Microsoft\Windows\CurrentVersion\Uninstall\StemSeparator-" + id;
    }

    private static void Uninstall(string root)
    {
        string id = InstallId(root);
        using (RegistryKey key = Registry.CurrentUser.OpenSubKey(UninstallKey(id)))
        {
            if (key == null || !String.Equals((string)key.GetValue("InstallLocation"), root, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("找不到此安装记录。");
        }
        string link = Path.Combine(Programs, Product + ".lnk");
        if (File.Exists(link))
        {
            object shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
            dynamic shortcut = ((dynamic)shell).CreateShortcut(link);
            try
            {
                string target = shortcut.TargetPath;
                if (target.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                    File.Delete(link);
            }
            finally
            {
                Marshal.FinalReleaseComObject(shortcut);
                Marshal.FinalReleaseComObject(shell);
            }
        }
        using (new FileStream(Path.Combine(root, ".setup-lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None))
        {
            foreach (string runtime in Directory.GetDirectories(root, "runtime-*"))
            {
                if (File.Exists(Path.Combine(runtime, Ready)) &&
                    (File.GetAttributes(runtime) & FileAttributes.ReparsePoint) == 0)
                    Directory.Delete(runtime, true);
            }
            Registry.CurrentUser.DeleteSubKeyTree(UninstallKey(id), false);
        }
    }

    private static bool HasNvidia()
    {
        try
        {
            using (var search = new ManagementObjectSearcher("SELECT Name FROM Win32_VideoController"))
            using (var result = search.Get())
                return result.Cast<ManagementObject>().Any(controller =>
                    Convert.ToString(controller["Name"]).IndexOf("NVIDIA", StringComparison.OrdinalIgnoreCase) >= 0);
        }
        catch (ManagementException) { return false; }
        catch (UnauthorizedAccessException) { return false; }
    }

    private static void Run(string file, string arguments, string directory,
                            Dictionary<string, string> environment, CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        var options = new ProcessStartInfo(file, arguments) {
            WorkingDirectory = directory, CreateNoWindow = true, UseShellExecute = false,
            RedirectStandardError = true, RedirectStandardOutput = true
        };
        options.EnvironmentVariables.Remove("PYTHONPATH");
        options.EnvironmentVariables.Remove("PYTHONHOME");
        if (environment != null)
            foreach (var pair in environment)
                options.EnvironmentVariables[pair.Key] = pair.Value;
        var lines = new Queue<string>();
        DataReceivedEventHandler collect = (sender, e) => {
            if (e.Data == null) return;
            lock (lines)
            {
                if (lines.Count == 12) lines.Dequeue();
                lines.Enqueue(e.Data);
            }
        };
        using (Process process = Process.Start(options))
        {
            process.OutputDataReceived += collect;
            process.ErrorDataReceived += collect;
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            using (cancellation.Register(() => StopProcess(process)))
                process.WaitForExit();
            cancellation.ThrowIfCancellationRequested();
            if (process.ExitCode != 0)
                throw new InvalidOperationException(String.Join("\n", lines));
        }
    }

    private static void StopProcess(Process process)
    {
        try
        {
            if (process.HasExited) return;
            var options = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "taskkill.exe"),
                "/PID " + process.Id + " /T /F") { UseShellExecute = false, CreateNoWindow = true };
            using (Process kill = Process.Start(options)) kill.WaitForExit();
        }
        catch (InvalidOperationException) { }
        catch (System.ComponentModel.Win32Exception) { }
    }

    private sealed class FolderOwner : Forms.IWin32Window
    {
        public IntPtr Handle { get; private set; }
        public FolderOwner(Window window) { Handle = new WindowInteropHelper(window).Handle; }
    }

    private sealed class Wizard
    {
        private readonly Window window;
        private readonly string uninstallRoot;
        private string parent = Path.Combine(Local, "Programs");
        private CancellationTokenSource cancellation;
        private bool done;
        private bool success;
        private bool userCancelled;

        public Wizard(Window window, string uninstallRoot)
        {
            this.window = window;
            this.uninstallRoot = uninstallRoot;
            Find<TextBlock>("VersionLabel").Text = "WINDOWS · v" + ReleaseInfo.Version;
            Find<Button>("Browse").Click += (sender, args) => Browse();
            Find<Button>("Cancel").Click += (sender, args) => Cancel();
            Find<Button>("Primary").Click += async (sender, args) => await Advance();
            window.Closing += (sender, args) => {
                if (cancellation != null) { Cancel(); args.Cancel = true; }
            };
            Find<CheckBox>("UseCuda").IsChecked = HasNvidia();
            Find<FrameworkElement>("Options").Visibility = uninstallRoot == null ? Visibility.Visible : Visibility.Collapsed;
            UpdatePath();
            SetState(uninstallRoot == null ? "安装音轨分离" : "卸载音轨分离",
                uninstallRoot == null ? "安装时会联网准备运行环境。模型就绪后，导入、识别、分离和导出都在本机进行。" :
                "应用程序将从此位置移除，已导出的 WAV 与本地模型会保留。");
            if (uninstallRoot != null) Find<Button>("Primary").Content = "卸载";
        }

        private T Find<T>(string name) where T : class { return (T)window.FindName(name); }

        private void SetState(string stage, string detail)
        {
            Find<TextBlock>("Stage").Text = stage;
            Find<TextBlock>("Detail").Text = detail;
        }

        private void UpdatePath()
        {
            var display = Find<TextBlock>("Destination");
            display.Text = GetRoot(parent);
            display.ToolTip = display.Text;
        }

        private void Browse()
        {
            using (var dialog = new Forms.FolderBrowserDialog())
            {
                dialog.Description = "选择音轨分离的安装位置";
                dialog.SelectedPath = parent;
                if (dialog.ShowDialog(new FolderOwner(window)) == Forms.DialogResult.OK)
                {
                    parent = dialog.SelectedPath;
                    UpdatePath();
                }
            }
        }

        private void Cancel()
        {
            if (cancellation == null) { window.Close(); return; }
            if (userCancelled) return;
            userCancelled = true;
            Find<Button>("Cancel").IsEnabled = false;
            SetState("正在取消…", "正在等待安装任务退出。");
            var token = cancellation;
            Task.Run(() => token.Cancel());
        }

        private async Task Advance()
        {
            if (done)
            {
                if (success && uninstallRoot == null)
                {
                    string runtime = Path.Combine(GetRoot(parent), "runtime-" + ReleaseInfo.Version +
                        (Find<CheckBox>("UseCuda").IsChecked == true ? "-cuda" : "-cpu"));
                    Process.Start(new ProcessStartInfo(Python(runtime, true), "-m stem_separator.gui.app --model-dir " +
                        Quote(Path.Combine(GetRoot(parent), ".model-cache"))) { WorkingDirectory = GetRoot(parent) });
                }
                window.Close();
                return;
            }
            var primary = Find<Button>("Primary");
            var cancel = Find<Button>("Cancel");
            var bar = Find<ProgressBar>("Progress");
            bool cuda = Find<CheckBox>("UseCuda").IsChecked == true;
            bool models = Find<CheckBox>("DownloadModels").IsChecked == true;
            Find<FrameworkElement>("Options").IsEnabled = false;
            primary.IsEnabled = false;
            cancel.IsEnabled = uninstallRoot == null;
            bar.Visibility = Visibility.Visible;
            bar.IsIndeterminate = true;
            cancellation = new CancellationTokenSource();
            userCancelled = false;
            SetState(uninstallRoot == null ? "正在安装" : "正在卸载", "正在准备…");
            try
            {
                var token = cancellation.Token;
                if (uninstallRoot == null)
                    await Task.Run(() => Install(parent, cuda, models,
                        (step, detail) => window.Dispatcher.InvokeAsync(() => {
                            if (!userCancelled) Find<TextBlock>("Detail").Text = detail;
                        }), token));
                else
                    await Task.Run(() => Uninstall(uninstallRoot));
                done = success = true;
                SetState(uninstallRoot == null ? "安装完成" : "卸载完成",
                    uninstallRoot != null ? "本地模型和导出的音轨已保留。" :
                    models ? "可从开始菜单启动。音频处理使用已准备的本地模型。" :
                    "可从开始菜单启动。请将本地模型复制到安装目录下的 .model-cache。");
                primary.Content = uninstallRoot == null ? "启动应用" : "关闭";
                cancel.Content = "完成";
            }
            catch (OperationCanceledException)
            {
                SetState("安装已取消", "可以重新开始。");
            }
            catch (Exception error)
            {
                File.WriteAllText(LogPath(), error.ToString());
                SetState(uninstallRoot == null ? "安装失败" : "卸载失败",
                    (error.Message.Split('\n').LastOrDefault(line => line.Trim().Length != 0) ?? "请查看安装日志。") +
                    "\n日志：" + LogPath());
            }
            finally
            {
                cancellation = null;
                bar.Visibility = Visibility.Collapsed;
                primary.IsEnabled = true;
                cancel.IsEnabled = true;
                Find<FrameworkElement>("Options").IsEnabled = !done;
            }
        }
    }
}
