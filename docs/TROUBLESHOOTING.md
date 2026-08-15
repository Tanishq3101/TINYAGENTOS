# Troubleshooting Guide

## Common Issues

### Model Loading Fails
**Error**: `FileNotFoundError: No such file or directory`

**Solution**:
```bash
python scripts/download_model.py
# Check if models/ directory exists
ls -la models/
```

### Out of Memory
**Error**: `MemoryError` or `CUDA out of memory`

**Solution**:
- Reduce context window: `LLM_N_CTX=1024`
- Enable quantization: Use INT4 or INT8 models
- Reduce batch size
- Monitor with: `watch nvidia-smi` (for GPU)

### API Connection Refused
**Error**: `Connection refused`

**Solution**:
```bash
# Check if service is running
docker-compose ps

# Restart service
docker-compose restart tinyagentos

# Check logs
docker-compose logs tinyagentos
```

### Slow Performance
**Solution**:
- Run benchmarks: `python scripts/run_benchmarks.py`
- Check resource usage: `htop` or Docker stats
- Profile code: `python -m cProfile`
